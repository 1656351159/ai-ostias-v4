"""Data contracts and validation for the AI-OSTIAS V4 OpenClaw runtime adapter.

V4 相对 V3 的契约变化（技术手册 5.2）：
- validate_task 新增 constraints.skill 字段（注册 Skill 名，如 "crawl4more"）。
- input 改为爬取任务参数（start_url/extra_urls/max_pages/priority/slice_timeout/
  db_path/job_id/mock_crawler）。
- output_schema 对齐 Skill 输出契约（job_id/status/processed_pages/
  extracted_count/failed_subtasks/error）。
- 新增稳定错误码 skill_execution_failed（Skill 侧失败/暂停/结果不可解析）。
- 新增稳定错误码 skill_cancelled（用户取消：Skill 如实上报 cancelled，
  RuntimeResult.status="cancelled"，不再归入 failed）。
- 网络访问仍需 allow_network: true 显式声明；爬取 Skill 强制要求 true。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit


NETWORK_TOOL_NAMES = {"web_search", "web_fetch", "browser"}

# 已在 Adapter 侧注册的 Skill 名。不支持任务级动态声明新 Skill（V3 安全哲学）。
REGISTERED_SKILLS = {"crawl4more"}

# Skill 输出契约（SKILL.md）：stdout 末行单行 JSON 的合法 status 取值。
# cancelled：用户在 DB 侧取消 Job 后 Skill 如实上报（语义为"已取消"，非"失败"）。
SKILL_RESULT_STATUSES = ("completed", "failed", "paused", "cancelled")

# output_schema 必须声明的 Skill 输出字段。
SKILL_OUTPUT_FIELDS = (
    "job_id",
    "status",
    "processed_pages",
    "extracted_count",
    "failed_subtasks",
    "error",
)

# 稳定错误码
ERROR_INVALID_TASK = "invalid_task"
ERROR_TOOL_POLICY_UNENFORCED = "tool_policy_unenforced"
ERROR_ENVIRONMENT_CHECK_FAILED = "environment_check_failed"
ERROR_TIMEOUT = "timeout"
ERROR_AGENT_EXECUTION_FAILED = "agent_execution_failed"
ERROR_INVALID_AGENT_RESULT = "invalid_agent_result"
ERROR_SKILL_EXECUTION_FAILED = "skill_execution_failed"  # V4 新增
ERROR_SKILL_CANCELLED = "skill_cancelled"  # 用户取消：终态但语义区别于失败


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class RuntimeErrorInfo:
    code: str
    message: str
    details: Optional[Dict[str, Any]] = None


@dataclass
class RuntimeResult:
    task_id: str
    runtime: str
    agent_id: str
    session_id: str
    status: str
    started_at: str
    finished_at: str
    duration_ms: int
    result: Optional[Dict[str, Any]] = None
    error: Optional[RuntimeErrorInfo] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _require_nonempty_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("%s must be a non-empty string" % path)
    return value


def _require_int(value: Any, path: str, minimum: int, maximum: int, default: int) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError("%s must be an integer between %d and %d" % (path, minimum, maximum))
    return value


def _validate_url(value: Any, path: str) -> str:
    url = _require_nonempty_string(value, path)
    try:
        parts = urlsplit(url)
    except ValueError:
        raise ValueError("%s is not a valid URL" % path)
    if parts.scheme not in ("http", "https"):
        raise ValueError("%s must use http or https" % path)
    if not parts.hostname:
        raise ValueError("%s must include a hostname" % path)
    if parts.username or parts.password:
        raise ValueError("%s must not embed credentials" % path)
    return url


def validate_task(task: Any) -> Dict[str, Any]:
    if not isinstance(task, dict):
        raise ValueError("task must be an object")

    _require_nonempty_string(task.get("task_id"), "task.task_id")
    _require_nonempty_string(task.get("task_type"), "task.task_type")
    _require_nonempty_string(task.get("goal"), "task.goal")

    task_input = task.get("input")
    if not isinstance(task_input, dict):
        raise ValueError("task.input must be an object")

    _validate_url(task_input.get("start_url"), "task.input.start_url")

    extra_urls = task_input.get("extra_urls", [])
    if not isinstance(extra_urls, list):
        raise ValueError("task.input.extra_urls must be an array")
    for index, url in enumerate(extra_urls):
        _validate_url(url, "task.input.extra_urls[%d]" % index)

    _require_int(task_input.get("max_pages"), "task.input.max_pages", 1, 500, 15)
    _require_int(task_input.get("priority"), "task.input.priority", -100, 100, 0)
    _require_int(task_input.get("slice_timeout"), "task.input.slice_timeout", 5, 600, 30)

    db_path = task_input.get("db_path")
    if db_path is not None:
        _require_nonempty_string(db_path, "task.input.db_path")
    job_id = task_input.get("job_id")
    if job_id is not None:
        _require_nonempty_string(job_id, "task.input.job_id")
    mock_crawler = task_input.get("mock_crawler", False)
    if not isinstance(mock_crawler, bool):
        raise ValueError("task.input.mock_crawler must be a boolean")

    constraints = task.get("constraints")
    if not isinstance(constraints, dict):
        raise ValueError("task.constraints must be an object")

    # V4 新增：Skill 声明字段。必须是已注册 Skill，不允许任务级声明新 Skill。
    skill = _require_nonempty_string(constraints.get("skill"), "task.constraints.skill")
    if skill not in REGISTERED_SKILLS:
        raise ValueError(
            "task.constraints.skill %r is not a registered skill" % skill
        )

    allow_network = constraints.get("allow_network", False)
    if not isinstance(allow_network, bool):
        raise ValueError("task.constraints.allow_network must be a boolean")
    allowed_tools = constraints.get("allowed_tools", [])
    if not isinstance(allowed_tools, list) or not all(
        isinstance(item, str) and item for item in allowed_tools
    ):
        raise ValueError("task.constraints.allowed_tools must be an array of strings")
    if not allow_network and set(allowed_tools) & NETWORK_TOOL_NAMES:
        raise ValueError("network tools require constraints.allow_network=true")
    # 爬取 Skill 必然发起网络请求（SKILL.md 副作用声明），必须显式声明。
    if skill == "crawl4more" and not allow_network:
        raise ValueError("crawl4more skill tasks require constraints.allow_network=true")

    timeout = constraints.get("timeout_seconds", 600)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 3600:
        raise ValueError("task.constraints.timeout_seconds must be between 1 and 3600")

    # output_schema 对齐 Skill 输出契约
    output_schema = task.get("output_schema")
    if not isinstance(output_schema, dict):
        raise ValueError("task.output_schema must be an object")
    missing_fields = [field for field in SKILL_OUTPUT_FIELDS if field not in output_schema]
    if missing_fields:
        raise ValueError(
            "task.output_schema must declare the skill output fields: %s"
            % ", ".join(missing_fields)
        )

    return task


def validate_agent_result(payload: Any, task: Dict[str, Any]) -> Dict[str, Any]:
    """校验 Skill 末行 stdout JSON（由 Agent 原样回传或经 Session 审计回收）。"""
    if not isinstance(payload, dict):
        raise ValueError("agent result must be an object")

    job_id = _require_nonempty_string(payload.get("job_id"), "agent result job_id")
    expected_job_id = task.get("input", {}).get("job_id")
    if expected_job_id and job_id != expected_job_id:
        raise ValueError("agent result job_id does not match the submitted task")

    status = payload.get("status")
    if status not in SKILL_RESULT_STATUSES:
        raise ValueError(
            "agent result status must be one of %s" % ", ".join(SKILL_RESULT_STATUSES)
        )

    normalized: Dict[str, Any] = {"job_id": job_id, "status": status}
    for field in ("processed_pages", "extracted_count", "failed_subtasks"):
        value = payload.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("agent result %s must be a non-negative integer" % field)
        normalized[field] = value

    error = payload.get("error")
    if error is not None and not isinstance(error, str):
        raise ValueError("agent result error must be null or a string")
    normalized["error"] = error

    return normalized
