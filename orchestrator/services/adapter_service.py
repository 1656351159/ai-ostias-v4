# services/adapter_service.py
"""RuntimeAdapter 与 crawl4more Skill 的装配层（只加载、不复制代码）。

模块名冲突说明：
- runtime-adapter/models.py 是模块，crawl4more-skill/models/ 是包，
  两者都通过顶层名 `models` 被各自代码 import，不能直接同时放 sys.path。
- 加载顺序：
  1) 以别名 v4_ra_models 加载 Adapter 的 models.py，并临时挂到 sys.modules["models"]；
  2) 以别名 v4_runtime_adapter 加载 runtime_adapter.py（其 `from models import ...`
     在 exec 时绑定到上面的临时模块）；
  3) 从 sys.modules 摘掉临时 "models"，把 Skill 的 models 包按真实包名加载
     （带 submodule_search_locations，`from .job import` 正常工作）；
  4) 以别名加载 Skill 的 services/task_manager.py（其 `from models.job import ...`
     此时解析到 Skill 的 models 包）。
- runtime_adapter 只在模块加载时 import models，之后不再查找，摘除安全。
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from config import ADAPTER_DIR, AGENT_ID, DB_PATH, PREFLIGHT_CACHE_TTL, SKILL_DIR


def _load_module(alias: str, path: Path, pkg_locations: Optional[list] = None):
    spec = importlib.util.spec_from_file_location(
        alias, str(path), submodule_search_locations=pkg_locations
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载模块: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


# ---------- 1-2. 加载 Runtime Adapter（纯标准库） ----------
_ra_models = _load_module("v4_ra_models", ADAPTER_DIR / "models.py")
sys.modules["models"] = _ra_models  # 临时占位，供 runtime_adapter 顶层 import
try:
    _adapter_mod = _load_module("v4_runtime_adapter", ADAPTER_DIR / "runtime_adapter.py")
finally:
    sys.modules.pop("models", None)  # 摘除，避免遮蔽 Skill 的 models 包

RuntimeAdapter = _adapter_mod.RuntimeAdapter
OpenClawAdapterError = _adapter_mod.OpenClawAdapterError

# ---------- 3-4. 加载 crawl4more Skill 的 models 包与 TaskManager ----------
_skill_models = _load_module(
    "models", SKILL_DIR / "models" / "__init__.py",
    pkg_locations=[str(SKILL_DIR / "models")],
)
_tm_mod = _load_module(
    "crawl4more_task_manager", SKILL_DIR / "services" / "task_manager.py"
)
TaskManager = _tm_mod.TaskManager
CrawlJob = _skill_models.CrawlJob
CrawlSubtask = _skill_models.CrawlSubtask

# ---------- 单例 ----------
adapter = RuntimeAdapter(agent_id=AGENT_ID)
task_manager = TaskManager(DB_PATH)


# ---------- 内存任务表（Adapter 侧运行状态佐证） ----------
class _TaskTable:
    """job_id -> 运行记录。dict 赋值在 GIL 下原子，加锁仅为读改写一致性。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: Dict[str, Dict[str, Any]] = {}

    def register(self, job_id: str, task: Dict[str, Any]) -> None:
        with self._lock:
            self._records[job_id] = {
                "job_id": job_id,
                "state": "submitted",  # submitted/running/done/failed
                "submitted_at": datetime.now().isoformat(),
                "finished_at": None,
                "task": task,
                "result": None,
                "error": None,
            }

    def mark_running(self, job_id: str) -> None:
        with self._lock:
            rec = self._records.get(job_id)
            if rec:
                rec["state"] = "running"

    def mark_done(self, job_id: str, result_dict: Dict[str, Any]) -> None:
        with self._lock:
            rec = self._records.get(job_id)
            if rec:
                rec["state"] = "done" if result_dict.get("status") == "completed" else "failed"
                rec["finished_at"] = datetime.now().isoformat()
                rec["result"] = result_dict
                err = result_dict.get("error")
                rec["error"] = (err or {}).get("message") if isinstance(err, dict) else None

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            rec = self._records.get(job_id)
            return dict(rec) if rec else None

    def summary(self, job_id: str) -> Optional[Dict[str, Any]]:
        """面向 /status 接口的摘要（不回传完整 task 契约）。"""
        rec = self.get(job_id)
        if not rec:
            return None
        out = {
            "state": rec["state"],
            "submitted_at": rec["submitted_at"],
            "finished_at": rec["finished_at"],
            "error": rec["error"],
        }
        result = rec.get("result") or {}
        out["duration_ms"] = result.get("duration_ms")
        payload = result.get("result")
        if isinstance(payload, dict):
            out["skill_result"] = {
                k: payload.get(k)
                for k in ("status", "processed_pages", "extracted_count",
                          "failed_subtasks", "error")
            }
        err = result.get("error")
        if isinstance(err, dict):
            out["error_code"] = err.get("code")
        return out


task_table = _TaskTable()
_background_tasks: set = set()


def _submit_sync(job_id: str, task: Dict[str, Any]) -> None:
    task_table.mark_running(job_id)
    result = adapter.submit(task)  # 同步阻塞（30-60s 起），跑在线程里
    task_table.mark_done(job_id, result.to_dict())


async def submit_background(job_id: str, task: Dict[str, Any]) -> None:
    """在线程中执行 Adapter.submit，事件循环不阻塞。"""
    await asyncio.to_thread(_submit_sync, job_id, task)


def launch_submit(job_id: str, task: Dict[str, Any]) -> None:
    """路由内调用：登记内存表并发射后台任务，接口立即返回。"""
    task_table.register(job_id, task)
    bg = asyncio.create_task(submit_background(job_id, task))
    _background_tasks.add(bg)
    bg.add_done_callback(_background_tasks.discard)


# ---------- preflight 60s 缓存 ----------
_preflight_lock = threading.Lock()
_preflight_cache: Dict[str, Any] = {"at": 0.0, "report": None}


def preflight_cached(ttl: int = PREFLIGHT_CACHE_TTL) -> Dict[str, Any]:
    now = time.monotonic()
    with _preflight_lock:
        if _preflight_cache["report"] is not None and now - _preflight_cache["at"] < ttl:
            return _preflight_cache["report"]
    report = adapter.preflight()  # 打 CLI，秒级，放锁外
    with _preflight_lock:
        _preflight_cache.update({"at": time.monotonic(), "report": report})
    return report
