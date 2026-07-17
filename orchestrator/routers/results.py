# routers/results.py - 结果查询 / 筛选 / 导出
import csv
import io
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse

from services import db_service

router = APIRouter(prefix="/api/results", tags=["results"])

EXPORT_COLUMNS = [
    ("id", "子任务ID"),
    ("job_uuid", "作业ID"),
    ("url", "URL"),
    ("site", "站点"),
    ("task_type", "类型"),
    ("title", "标题"),
    ("pub_date", "发布日期"),
    ("site_name", "来源名称"),
    ("content", "正文"),
    ("created_at", "创建时间"),
    ("completed_at", "完成时间"),
]


def _filters(created_from, created_to, url_kw, site, task_type, sort, order):
    return dict(
        created_from=created_from, created_to=created_to, url_kw=url_kw,
        site=site, task_type=task_type, sort=sort, order=order,
    )


@router.get("")
async def list_results(
    created_from: Optional[str] = Query(None, description="创建时间起（ISO）"),
    created_to: Optional[str] = Query(None, description="创建时间止（ISO）"),
    url_kw: Optional[str] = Query(None, description="URL 关键词"),
    site: Optional[str] = Query(None, description="站点域名，如 cars.org.cn"),
    task_type: Optional[str] = Query(None, description="seed/article/nav"),
    sort: str = Query("created_at", description="created_at 或 url"),
    order: str = Query("desc", description="asc 或 desc"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
):
    try:
        return db_service.query_results(
            **_filters(created_from, created_to, url_kw, site, task_type,
                       sort, order),
            page=page, size=size,
        )
    except db_service.ControlError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/export")
async def export_results(
    format: str = Query("csv", description="csv 或 json"),
    created_from: Optional[str] = None,
    created_to: Optional[str] = None,
    url_kw: Optional[str] = None,
    site: Optional[str] = None,
    task_type: Optional[str] = None,
    sort: str = "created_at",
    order: str = "desc",
):
    try:
        items = db_service.query_results_all(
            **_filters(created_from, created_to, url_kw, site, task_type,
                       sort, order)
        )
    except db_service.ControlError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if format == "json":
        return JSONResponse(
            content={"total": len(items), "items": items},
            headers={"Content-Disposition": "attachment; filename=results.json"},
        )
    if format != "csv":
        raise HTTPException(status_code=400, detail="format 只能是 csv 或 json")

    buffer = io.StringIO()
    buffer.write("﻿")  # BOM，Excel 打开中文不乱码
    writer = csv.writer(buffer)
    writer.writerow([label for _, label in EXPORT_COLUMNS])
    for item in items:
        writer.writerow([
            (item.get(key) or "").replace("\r", " ").replace("\n", " ")
            if isinstance(item.get(key), str) else item.get(key)
            for key, _ in EXPORT_COLUMNS
        ])
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=results.csv"},
    )
