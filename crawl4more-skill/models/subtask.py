# models/subtask.py
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any
import json


@dataclass
class CrawlSubtask:
    """子任务模型"""
    id: Optional[int] = None
    job_id: int = 0
    url: str = ""
    task_type: str = "seed"  # seed/article/nav
    status: str = "pending"  # pending/running/completed/failed
    retry_count: int = 0
    max_retries: int = 3
    extracted_data: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        """转换为字典（用于 JSON 序列化）"""
        result = {}
        for k, v in self.__dict__.items():
            if k == 'extracted_data' and v:
                result[k] = json.dumps(v, ensure_ascii=False)
            elif isinstance(v, datetime):
                result[k] = v.isoformat()
            else:
                result[k] = v
        return result

    @classmethod
    def from_row(cls, row: dict) -> "CrawlSubtask":
        """从数据库行创建实例"""
        data = dict(row)
        # 转换 JSON 字段
        if data.get('extracted_data') and isinstance(data['extracted_data'], str):
            data['extracted_data'] = json.loads(data['extracted_data'])
        # 转换时间字段
        for k in ['started_at', 'completed_at', 'created_at', 'updated_at']:
            if data.get(k) and isinstance(data[k], str):
                data[k] = datetime.fromisoformat(data[k])
        return cls(**data)
