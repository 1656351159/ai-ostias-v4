# routers/jobs.py - Job 提交 / 列表 / 状态轮询 / 人工干预
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from config import DB_PATH
from services import db_service
from services.adapter_service import launch_submit, task_table

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

SKILL_OUTPUT_SCHEMA = {
    "job_id": "string",
    "status": "completed|failed|paused",
    "processed_pages": "integer",
    "extracted_count": "integer",
    "failed_subtasks": "integer",
    "error": "string|null",
}


def _check_url(value: str) -> str:
    if not isinstance(value, str) or not value.startswith(("http://", "https://")):
        raise ValueError("URL 必须以 http:// 或 https:// 开头")
    return value


class JobCreateRequest(BaseModel):
    start_url: str
    extra_urls: List[str] = Field(default_factory=list)
    max_pages: int = Field(default=15, ge=1, le=500)
    priority: int = Field(default=0, ge=-100, le=100)
    slice_timeout: int = Field(default=30, ge=5, le=600)
    mock: bool = Field(default=False, description="演示模式：Skill 以 mock 爬取")
    keywords: Optional[str] = None
    timeout_seconds: int = Field(default=600, ge=60, le=3600)

    @field_validator("start_url")
    @classmethod
    def _start_url_valid(cls, v: str) -> str:
        return _check_url(v)

    @field_validator("extra_urls")
    @classmethod
    def _extra_urls_valid(cls, v: List[str]) -> List[str]:
        if len(v) > 50:
            raise ValueError("extra_urls 最多 50 条")
        return [_check_url(u) for u in v]


class ControlRequest(BaseModel):
    action: str = Field(..., description="pause/resume/cancel/update")
    params: Dict[str, Any] = Field(default_factory=dict)


@router.post("")
async def create_job(req: JobCreateRequest):
    """确认参数后创建 Job：生成 job_id → 组装任务契约 → 异步 Adapter.submit。

    按 M3 对齐方案，Orchestrator 不预写 Job 行：Agent 执行 run.py 时由
    Skill 自己建行，/status 接口在 DB 行出现前以内存表状态作答。
    """
    job_id = str(uuid.uuid4())
    goal = req.keywords or f"爬取 {req.start_url} 的科技情报（最多 {req.max_pages} 页）"
    task = {
        "task_id": f"job-{job_id}",
        "task_type": "web_intelligence_crawl",
        "goal": goal,
        "input": {
            "start_url": req.start_url,
            "extra_urls": req.extra_urls,
            "max_pages": req.max_pages,
            "priority": req.priority,
            "slice_timeout": req.slice_timeout,
            "db_path": DB_PATH,
            "job_id": job_id,
            "mock_crawler": req.mock,
        },
        "constraints": {
            "skill": "crawl4more",
            "allow_network": True,
            "allowed_tools": ["read", "exec"],
            "timeout_seconds": req.timeout_seconds,
        },
        "output_schema": SKILL_OUTPUT_SCHEMA,
    }
    launch_submit(job_id, task)
    return {"job_id": job_id, "status": "submitted"}


@router.get("")
async def list_jobs(limit: int = 100):
    limit = max(1, min(limit, 500))
    jobs = db_service.list_jobs(limit)
    for job in jobs:
        summary = task_table.summary(job["job_uuid"])
        job["adapter_state"] = summary["state"] if summary else None
    return {"total": len(jobs), "items": jobs}


@router.get("/{job_id}/status")
async def job_status(job_id: str):
    """前端轮询主接口：Job + SubTask + 最近 50 条日志 + Adapter 内存状态。"""
    adapter_state = task_table.summary(job_id)
    job = db_service.get_job_by_uuid(job_id)
    if job is None:
        if adapter_state is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        # Skill 尚未建行（Agent 仍在启动阶段），以内存状态作答
        return {
            "job": None,
            "subtasks": [],
            "logs": [],
            "adapter": adapter_state,
            "note": "任务已提交，Agent 正在启动，数据库记录尚未创建",
        }
    return {
        "job": job,
        "subtasks": db_service.list_subtasks(job["id"]),
        "logs": db_service.list_logs(job["id"], limit=50),
        "adapter": adapter_state,
    }


@router.post("/{job_id}/control")
async def control_job(job_id: str, req: ControlRequest):
    job = db_service.get_job_by_uuid(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="任务不存在或数据库记录尚未创建（Agent 启动中，请稍后重试）",
        )
    try:
        if req.action == "pause":
            updated = db_service.pause_job(job)
        elif req.action == "resume":
            updated = db_service.resume_job(job)
        elif req.action == "cancel":
            updated = db_service.cancel_job(job)
        elif req.action == "update":
            updated = db_service.update_job_params(
                job, req.params.get("max_pages"), req.params.get("priority")
            )
        else:
            raise HTTPException(
                status_code=400,
                detail="不支持的 action，只能是 pause/resume/cancel/update",
            )
    except db_service.ControlError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"job": updated, "adapter": task_table.summary(job_id)}
