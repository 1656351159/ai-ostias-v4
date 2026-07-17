# services/db_service.py
"""SQLite 访问层：复用 Skill 的 schema/TaskManager，直接 SQL 做查询与控制。

连接策略：与 TaskManager 一致——每次操作新建连接、用完即关（无跨线程共享
连接，天然规避 check_same_thread 问题；SQLite 写锁由短事务保证）。
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from config import DB_PATH, SCHEMA_SQL

JOB_STATUSES = ("pending", "running", "paused", "completed", "failed", "cancelled")
TERMINAL_STATUSES = ("completed", "failed", "cancelled")
# 人工暂停的锁标记：调度器 get_next_runnable_job 只捞 pending/paused 且未锁的
# Job，写一条远端未来的锁可让用户暂停不被时间片轮换自动复活。
MANUAL_PAUSE_LOCK = "manual_pause"
FAR_FUTURE = "9999-12-31T23:59:59"


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """启动时幂等建表。"""
    Path(DB_PATH).resolve().parent.mkdir(parents=True, exist_ok=True)
    with open(SCHEMA_SQL, "r", encoding="utf-8") as f:
        schema = f.read()
    with get_conn() as conn:
        conn.executescript(schema)


def check_db() -> str:
    """system/status 用：返回 ok 或错误信息。"""
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1").fetchone()
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            missing = {"crawl_jobs", "crawl_subtasks", "crawl_task_logs"} - tables
            if missing:
                return "缺少数据表: %s" % ", ".join(sorted(missing))
        return "ok"
    except Exception as exc:  # noqa: BLE001 - 状态接口需要把错误暴露出来
        return str(exc)


def _now() -> str:
    return datetime.now().isoformat()


def _add_log(conn, job_pk: int, action: str, old: Optional[str],
             new: Optional[str], message: str) -> None:
    conn.execute(
        "INSERT INTO crawl_task_logs (job_id, subtask_id, action, old_status,"
        " new_status, worker, message, created_at) VALUES (?, NULL, ?, ?, ?,"
        " 'orchestrator', ?, ?)",
        (job_pk, action, old, new, message, _now()),
    )


# ==================== Job 查询 ====================

def list_jobs(limit: int = 100) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM crawl_jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_job_by_uuid(job_uuid: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM crawl_jobs WHERE job_uuid = ?", (job_uuid,)
        ).fetchone()
    return dict(row) if row else None


def list_subtasks(job_pk: int) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, job_id, url, task_type, status, retry_count, error_message,"
            " started_at, completed_at, created_at, updated_at,"
            " (extracted_data IS NOT NULL) AS has_extracted_data"
            " FROM crawl_subtasks WHERE job_id = ?"
            " ORDER BY created_at ASC",
            (job_pk,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_logs(job_pk: int, limit: int = 50) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM crawl_task_logs WHERE job_id = ?"
            " ORDER BY created_at DESC LIMIT ?",
            (job_pk, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ==================== 人工干预（写库即控制） ====================

class ControlError(Exception):
    """业务层控制错误，消息为中文，路由层转 400/409。"""


def pause_job(job: Dict[str, Any]) -> Dict[str, Any]:
    if job["status"] in TERMINAL_STATUSES:
        raise ControlError(f"任务已处于终态（{job['status']}），无法暂停")
    if job["status"] == "paused" and job.get("locked_by") == MANUAL_PAUSE_LOCK:
        raise ControlError("任务已是暂停状态")
    old = job["status"]
    with get_conn() as conn:
        conn.execute(
            "UPDATE crawl_jobs SET status='paused', current_slice_start=NULL,"
            " locked_by=?, locked_until=?, updated_at=? WHERE id=?",
            (MANUAL_PAUSE_LOCK, FAR_FUTURE, _now(), job["id"]),
        )
        _add_log(conn, job["id"], "manual_pause", old, "paused", "用户暂停任务")
    return get_job_by_uuid(job["job_uuid"])


def resume_job(job: Dict[str, Any]) -> Dict[str, Any]:
    if job["status"] != "paused":
        raise ControlError(f"当前状态为 {job['status']}，只有暂停中的任务可以恢复")
    # 从未跑过的任务回到 pending 由调度器正常捞起；跑过的回 running，
    # 等待中的 Worker 下一轮重读即继续（避免调度器重复捞取产生第二个 Worker）。
    new_status = "running" if job.get("started_at") else "pending"
    with get_conn() as conn:
        conn.execute(
            "UPDATE crawl_jobs SET status=?, locked_by=NULL, locked_until=NULL,"
            " updated_at=? WHERE id=?",
            (new_status, _now(), job["id"]),
        )
        _add_log(conn, job["id"], "manual_resume", "paused", new_status, "用户恢复任务")
    return get_job_by_uuid(job["job_uuid"])


def cancel_job(job: Dict[str, Any]) -> Dict[str, Any]:
    if job["status"] in TERMINAL_STATUSES:
        raise ControlError(f"任务已处于终态（{job['status']}），无法取消")
    old = job["status"]
    now = _now()
    with get_conn() as conn:
        conn.execute(
            "UPDATE crawl_jobs SET status='cancelled', completed_at=?,"
            " locked_by=NULL, locked_until=NULL, current_slice_start=NULL,"
            " updated_at=? WHERE id=?",
            (now, now, job["id"]),
        )
        _add_log(conn, job["id"], "manual_cancel", old, "cancelled", "用户取消任务")
    return get_job_by_uuid(job["job_uuid"])


def update_job_params(job: Dict[str, Any], max_pages: Optional[int],
                      priority: Optional[int]) -> Dict[str, Any]:
    if job["status"] in TERMINAL_STATUSES:
        raise ControlError(f"任务已处于终态（{job['status']}），无法修改参数")
    if max_pages is None and priority is None:
        raise ControlError("update 动作需要在 params 中给出 max_pages 或 priority")
    sets, values, changes = [], [], []
    if max_pages is not None:
        if not isinstance(max_pages, int) or not 1 <= max_pages <= 500:
            raise ControlError("max_pages 必须是 1-500 的整数")
        sets.append("max_pages=?")
        values.append(max_pages)
        changes.append(f"max_pages {job['max_pages']} -> {max_pages}")
    if priority is not None:
        if not isinstance(priority, int) or not -100 <= priority <= 100:
            raise ControlError("priority 必须是 -100 到 100 的整数")
        sets.append("priority=?")
        values.append(priority)
        changes.append(f"priority {job['priority']} -> {priority}")
    sets.append("updated_at=?")
    values.append(_now())
    values.append(job["id"])
    with get_conn() as conn:
        conn.execute(
            f"UPDATE crawl_jobs SET {', '.join(sets)} WHERE id=?", values
        )
        _add_log(conn, job["id"], "manual_update", job["status"], job["status"],
                 "用户修改参数: " + "; ".join(changes))
    return get_job_by_uuid(job["job_uuid"])


# ==================== 结果查询 ====================

def _flatten(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    raw = item.pop("extracted_data", None)
    title = pub_date = site_name = content = None
    if raw:
        try:
            data = json.loads(raw)
            text = (data or {}).get("raw_text") or {}
            title = text.get("title")
            pub_date = text.get("pub_date")
            site_name = text.get("site_name")
            content = text.get("content")
        except (json.JSONDecodeError, AttributeError):
            pass
    try:
        site = urlsplit(item["url"]).hostname or ""
    except ValueError:
        site = ""
    item.update({
        "site": site,
        "title": title,
        "pub_date": pub_date,
        "site_name": site_name,
        "content": content,
    })
    return item


def query_results(created_from: Optional[str] = None,
                  created_to: Optional[str] = None,
                  url_kw: Optional[str] = None,
                  site: Optional[str] = None,
                  task_type: Optional[str] = None,
                  sort: str = "created_at",
                  order: str = "desc",
                  page: int = 1,
                  size: int = 20) -> Dict[str, Any]:
    where = ["s.status = 'completed'", "s.extracted_data IS NOT NULL"]
    params: List[Any] = []
    if created_from:
        where.append("s.created_at >= ?")
        params.append(created_from)
    if created_to:
        where.append("s.created_at <= ?")
        params.append(created_to)
    if url_kw:
        where.append("s.url LIKE ?")
        params.append(f"%{url_kw}%")
    if site:
        where.append("s.url LIKE ?")
        params.append(f"%://{site}%")
    if task_type:
        if task_type not in ("seed", "article", "nav"):
            raise ControlError("task_type 只能是 seed/article/nav")
        where.append("s.task_type = ?")
        params.append(task_type)
    if sort not in ("created_at", "url"):
        raise ControlError("sort 只能是 created_at 或 url")
    if order not in ("asc", "desc"):
        raise ControlError("order 只能是 asc 或 desc")

    where_sql = " AND ".join(where)
    with get_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM crawl_subtasks s"
            f" JOIN crawl_jobs j ON j.id = s.job_id WHERE {where_sql}",
            params,
        ).fetchone()["cnt"]
        rows = conn.execute(
            f"SELECT s.id, s.url, s.task_type, s.status, s.extracted_data,"
            f" s.retry_count, s.created_at, s.completed_at, j.job_uuid"
            f" FROM crawl_subtasks s JOIN crawl_jobs j ON j.id = s.job_id"
            f" WHERE {where_sql}"
            f" ORDER BY s.{sort} {order.upper()}, s.id ASC"
            f" LIMIT ? OFFSET ?",
            params + [size, (page - 1) * size],
        ).fetchall()
    return {
        "total": total,
        "page": page,
        "size": size,
        "items": [_flatten(r) for r in rows],
    }


def query_results_all(created_from=None, created_to=None, url_kw=None,
                      site=None, task_type=None, sort="created_at",
                      order="desc", cap: int = 5000) -> List[Dict[str, Any]]:
    """导出用：同一筛选逻辑，不分页（封顶 cap 条）。"""
    result = query_results(created_from, created_to, url_kw, site, task_type,
                           sort, order, page=1, size=cap)
    return result["items"]
