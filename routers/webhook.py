"""WhatsApp webhook route — receives incoming messages from the bridge."""

import asyncio
import secrets

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from config.settings import settings
from core.whatsapp_identity import is_allowed
from schemas.webhook import WhatsAppWebhookPayload

logger = structlog.get_logger()

router = APIRouter()


@router.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    from interactions.webhook_handler import handle_incoming_message

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse(
            status_code=401,
            content={"error": "Unauthorized - missing or invalid Authorization header"},
        )

    token = auth_header.replace("Bearer ", "")
    if not settings.whatsapp_bridge_token:
        return JSONResponse(
            status_code=500,
            content={"error": "Server misconfigured - no webhook token"},
        )

    if not secrets.compare_digest(token, settings.whatsapp_bridge_token):
        return JSONResponse(status_code=403, content={"error": "Forbidden - invalid token"})

    try:
        data = await request.json()
    except Exception as exc:
        logger.warning(f"Failed to parse webhook JSON: {type(exc).__name__}: {exc}")
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    try:
        payload = WhatsAppWebhookPayload(**data)
    except ValidationError as exc:
        logger.warning(f"Webhook validation error: {exc}")
        return JSONResponse(
            status_code=422,
            content={"error": "Validation error", "details": [err["msg"] for err in exc.errors()]},
        )

    message_text = payload.message.conversation.strip()
    remote_jid = payload.key.remoteJid.strip()
    if not message_text or not remote_jid:
        return JSONResponse(status_code=400, content={"error": "Message and remoteJid cannot be empty"})

    if not is_allowed(remote_jid, settings.allowed_numbers):
        return JSONResponse(status_code=200, content={"status": "ignored", "reason": "not_allowed"})

    task = asyncio.create_task(handle_incoming_message(remote_jid, message_text))
    task.add_done_callback(
        lambda current_task: logger.error(f"Task failed: {current_task.exception()}")
        if current_task.exception()
        else logger.info(f"Message processed for {remote_jid}")
    )
    return JSONResponse(status_code=200, content={"status": "queued"})
