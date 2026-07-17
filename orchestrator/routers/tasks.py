# routers/tasks.py - POST /api/tasks/parse
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.parse_service import parse_text

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class ParseRequest(BaseModel):
    text: str = Field(..., description="自然语言任务描述")


@router.post("/parse")
async def parse_task(req: ParseRequest):
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="输入文本不能为空")
    if len(text) > 4000:
        raise HTTPException(status_code=400, detail="输入文本过长（上限 4000 字符）")
    draft = await parse_text(text)
    return draft
