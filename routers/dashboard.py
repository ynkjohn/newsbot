"""Dashboard routes — serves the HTML page and exposes dashboard data."""

from pathlib import Path

import httpx
import structlog
from fastapi import APIRouter, Depends

from config.settings import settings
from db.engine import async_session
from interactions.admin_auth import require_admin
from interactions.dashboard_data import build_dashboard_payload

logger = structlog.get_logger()

router = APIRouter()

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


async def fetch_whatsapp_status() -> dict:
    headers = {}
    if settings.whatsapp_bridge_token:
        headers["Authorization"] = f"Bearer {settings.whatsapp_bridge_token}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{settings.whatsapp_bridge_url}/status", headers=headers)
            response.raise_for_status()
            payload = response.json()
            return {
                "status": payload.get("status", "unknown"),
                "connected": payload.get("status") == "connected",
            }
    except Exception as exc:
        logger.warning(f"Failed to fetch WhatsApp Bridge status: {type(exc).__name__}: {exc}")
        return {"status": "unreachable", "connected": False}


def _admin_dashboard_response():
    from fastapi.responses import FileResponse

    return FileResponse(_STATIC_DIR / "dashboard.html")


@router.get("/", dependencies=[Depends(require_admin)])
async def dashboard_root():
    return _admin_dashboard_response()


@router.get("/dashboard", dependencies=[Depends(require_admin)])
async def dashboard_page():
    return _admin_dashboard_response()


@router.get("/api/dashboard", dependencies=[Depends(require_admin)])
async def dashboard():
    bridge_status = await fetch_whatsapp_status()
    async with async_session() as session:
        return await build_dashboard_payload(session, bridge_status)


@router.get("/api/whatsapp-status", dependencies=[Depends(require_admin)])
async def whatsapp_status():
    return await fetch_whatsapp_status()
