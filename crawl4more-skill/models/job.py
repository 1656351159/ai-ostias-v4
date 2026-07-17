# models/job.py
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import uuid


@dataclass
class CrawlJob:
    """爬虫作业模型"""
    id: Optional[int] = None
    job_uuid: str = field(default_factory=lambda: str(uuid.uuid4()))
    start_url: str = ""
    status: str = "pending"  # pending/running/paused/completed/failed/cancelled
    max_pages: int = 15
    concurrency: int = 3
    priority: int = 0
    processed_pages: int = 0
    total_pages: int = 0
    slice_timeout: int = 30
    current_slice_start: Optional[datetime] = None
    locked_by: Optional[str] = None
    locked_until: Optional[datetime] = None
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        """转换为字典（用于 JSON 序列化）"""
        result = {}
        for k, v in self.__dict__.items():
            if isinstance(v, datetime):
                result[k] = v.isoformat()
            else:
                result[k] = v
        return result

    @classmethod
    def from_row(cls, row: dict) -> "CrawlJob":
        """从数据库行创建实例"""
        data = dict(row)
        # 转换时间字段
        for k in ['current_slice_start', 'locked_until', 'created_at',
                  'updated_at', 'started_at', 'completed_at']:
            if data.get(k) and isinstance(data[k], str):
                data[k] = datetime.fromisoformat(data[k])
        return cls(**data)