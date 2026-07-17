# routers/system.py - GET /api/system/status
import asyncio

from fastapi import APIRouter

from config import VERSION
from services import db_service
from services.adapter_service import preflight_cached

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/status")
async def system_status():
    db_state = await asyncio.to_thread(db_service.check_db)
    try:
        preflight = await asyncio.to_thread(preflight_cached)
        openclaw = {
            "ok": preflight.get("ok"),
            "version": preflight.get("version"),
            "mode": preflight.get("mode"),
            "transport": preflight.get("transport"),
            "agent_id": preflight.get("agent_id"),
            "gateway_url": preflight.get("gateway_url"),
            "checks": preflight.get("checks", []),
            "cached": True,
        }
    except Exception as exc:  # noqa: BLE001 - 状态接口不应 500
        openclaw = {"ok": False, "error": str(exc)}
    return {"db": db_state, "openclaw": openclaw, "version": VERSION}
