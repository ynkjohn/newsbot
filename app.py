import asyncio
import json
import secrets
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Request
from pydantic import BaseModel, Field, field_validator

from config.settings import settings
from db.engine import init_db
from collector.sources import seed_feeds_if_empty
from scheduler.jobs import (
    run_morning_pipeline,
    run_midday_pipeline,
    run_afternoon_pipeline,
    run_evening_pipeline,
    cleanup_old_articles,
    check_feed_health,
)

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
)

logger = structlog.get_logger()

def _agent_debug_log_path() -> Path:
    """NDJSON path on host when using Docker volume ``./data:/app/data``."""
    return settings.base_dir / "data" / "debug-8222de.log"


def _agent_debug_log(
    location: str,
    message: str,
    hypothesis_id: str,
    data: dict,
    run_id: str = "pre",
) -> None:
    # region agent log
    try:
        path = _agent_debug_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = (
            json.dumps(
                {
                    "sessionId": "8222de",
                    "runId": run_id,
                    "hypothesisId": hypothesis_id,
                    "location": location,
                    "message": message,
                    "data": data,
                    "timestamp": int(time.time() * 1000),
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
        # Mirror to stderr so `docker compose logs newsbot` captures session even if volume sync lags
        sys.stderr.write(line)
        sys.stderr.flush()
    except Exception as e:
        try:
            sys.stderr.write(
                json.dumps(
                    {
                        "sessionId": "8222de",
                        "runId": run_id,
                        "hypothesisId": hypothesis_id,
                        "location": location,
                        "message": "agent_debug_log_failed",
                        "data": {"exc_type": type(e).__name__},
                        "timestamp": int(time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            sys.stderr.flush()
        except Exception:
            pass
    # endregion


# Pydantic models for webhook validation
class WhatsAppKeyPayload(BaseModel):
    """Remote JID key from WhatsApp webhook."""
    remoteJid: str = Field(..., min_length=1)


class WhatsAppMessagePayload(BaseModel):
    """Message content from WhatsApp webhook."""
    conversation: str = Field(..., min_length=1)


class WhatsAppWebhookPayload(BaseModel):
    """Full payload from whatsapp-bridge webhook.
    
    Structure:
    {
        "key": {"remoteJid": "551234567890@s.whatsapp.net"},
        "message": {"conversation": "text message"}
    }
    """
    key: WhatsAppKeyPayload
    message: WhatsAppMessagePayload

    @field_validator('key', mode='before')
    @classmethod
    def validate_key(cls, v):
        if not isinstance(v, dict):
            raise ValueError("key must be a dict")
        return v

    @field_validator('message', mode='before')
    @classmethod
    def validate_message(cls, v):
        if not isinstance(v, dict):
            raise ValueError("message must be a dict")
        return v

from fastapi.responses import JSONResponse

scheduler = AsyncIOScheduler(timezone=settings.timezone)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting NewsBot...")
    await init_db()
    logger.info("Database initialized")
    await seed_feeds_if_empty()
    logger.info("Feeds seeded")

    _pipeline_jobs = [
        ("morning",   run_morning_pipeline),
        ("midday",    run_midday_pipeline),
        ("afternoon", run_afternoon_pipeline),
        ("evening",   run_evening_pipeline),
    ]
    hours = [int(h.strip()) for h in settings.pipeline_hours.split(",")]
    for hour, (period, func) in zip(hours, _pipeline_jobs):
        scheduler.add_job(
            func,
            CronTrigger(hour=hour, minute=0),
            id=f"{period}_pipeline",
            max_instances=1,
            misfire_grace_time=900,
        )
        logger.info(f"Scheduled {period} pipeline at {hour:02d}:00")
    scheduler.add_job(
        cleanup_old_articles,
        CronTrigger(hour=0, minute=30),
        id="cleanup",
        max_instances=1,
    )
    scheduler.add_job(
        check_feed_health,
        CronTrigger(hour="*/6"),
        id="feed_health",
        max_instances=1,
    )

    scheduler.start()
    logger.info("Scheduler started", pipeline_hours=settings.pipeline_hours)

    yield

    scheduler.shutdown()
    logger.info("Scheduler shut down")


app = FastAPI(title="NewsBot", version="0.1.0", lifespan=lifespan)

# Serve static files (dashboard)
from fastapi.staticfiles import StaticFiles
import os
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/health")
async def health():
    return {"status": "ok", "service": "newsbot"}


@app.get("/api/dashboard")
async def dashboard():
    """API endpoint for dashboard data."""
    from sqlalchemy import select, func
    from db.engine import async_session
    from db.models import NewsArticle, PipelineRun, Summary, Subscriber

    def _normalize_source_ids(raw_ids) -> list[int]:
        if not isinstance(raw_ids, list):
            return []
        return [article_id for article_id in raw_ids if isinstance(article_id, int)]

    def _extract_bullets(raw_takeaways) -> list[str]:
        if isinstance(raw_takeaways, dict):
            bullets = raw_takeaways.get("bullets", [])
            return [str(item) for item in bullets if item]
        if isinstance(raw_takeaways, list):
            return [str(item) for item in raw_takeaways if item]
        return []

    def _extract_insight(raw_takeaways) -> str:
        if isinstance(raw_takeaways, dict):
            return str(raw_takeaways.get("insight", "") or "")
        return ""

    def _ordered_source_urls(source_ids: list[int], source_url_map: dict[int, str]) -> list[str]:
        ordered_urls: list[str] = []
        seen_urls: set[str] = set()
        for article_id in source_ids:
            url = source_url_map.get(article_id)
            if url and url not in seen_urls:
                ordered_urls.append(url)
                seen_urls.add(url)
        return ordered_urls

    def _serialize_summary(summary: Summary) -> dict:
        source_ids = _normalize_source_ids(getattr(summary, "source_article_ids", []))
        source_urls = _ordered_source_urls(source_ids, source_url_map)
        return {
            "id": summary.id,
            "header": summary.summary_text.split('\n')[0] if summary.summary_text else f"Resumo {summary.category}",
            "category": summary.category,
            "period": summary.period,
            "date": summary.date.isoformat() if summary.date else None,
            "bullets": _extract_bullets(summary.key_takeaways),
            "insight": _extract_insight(summary.key_takeaways),
            "summaryText": summary.summary_text or "",
            "sourceUrls": source_urls,
            "sourceCount": len(source_urls),
            "created_at": summary.created_at.isoformat() if summary.created_at else None,
        }
    
    async with async_session() as session:
        # Count active subscribers only
        sub_result = await session.execute(
            select(func.count(Subscriber.id)).where(Subscriber.active == True)  # noqa: E712
        )
        subscriber_count = sub_result.scalar() or 0
        
        # Get today's summaries by logical summary date, not creation timestamp
        today = datetime.now(timezone.utc).date()
        summaries_result = await session.execute(
            select(Summary)
            .where(Summary.date == today)
            .order_by(Summary.created_at.desc())
        )
        summaries = summaries_result.scalars().all()
        pending_result = await session.execute(
            select(func.count(Summary.id)).where(
                Summary.date == today,
                Summary.sent_at.is_(None),
            )
        )
        pending_summaries = pending_result.scalar() or 0

        recent_runs_result = await session.execute(
            select(PipelineRun)
            .order_by(PipelineRun.started_at.desc())
            .limit(6)
        )
        recent_runs = recent_runs_result.scalars().all()

        unique_source_ids: list[int] = []
        for summary in summaries:
            for article_id in _normalize_source_ids(getattr(summary, "source_article_ids", [])):
                if article_id not in unique_source_ids:
                    unique_source_ids.append(article_id)

        source_url_map: dict[int, str] = {}
        if unique_source_ids:
            source_rows_result = await session.execute(
                select(NewsArticle.id, NewsArticle.url).where(NewsArticle.id.in_(unique_source_ids))
            )
            source_url_map = {
                article_id: url
                for article_id, url in source_rows_result.all()
            }
        
        return {
            "subscribers": subscriber_count,
            "todaySummaries": len(summaries),
            "nextSend": settings.pipeline_schedule_display,
            "pipelineHours": settings.pipeline_hours_list,
            "timezone": settings.timezone,
            "pendingSummaries": pending_summaries,
            "recentRuns": [
                {
                    "id": run.id,
                    "period": run.period,
                    "status": run.status,
                    "articlesCollected": run.articles_collected,
                    "summariesGenerated": run.summaries_generated,
                    "messagesSent": run.messages_sent,
                    "startedAt": run.started_at.isoformat() if run.started_at else None,
                    "finishedAt": run.finished_at.isoformat() if run.finished_at else None,
                }
                for run in recent_runs
            ],
            "summaries": [_serialize_summary(summary) for summary in summaries]
        }


@app.get("/api/whatsapp-status")
async def whatsapp_status():
    headers = {}
    if settings.whatsapp_bridge_token:
        headers["Authorization"] = f"Bearer {settings.whatsapp_bridge_token}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{settings.whatsapp_bridge_url}/status",
                headers=headers,
            )
            response.raise_for_status()
            payload = response.json()
            return {
                "status": payload.get("status", "unknown"),
                "connected": payload.get("status") == "connected",
            }
    except Exception as e:
        logger.warning(f"Failed to fetch WhatsApp Bridge status: {type(e).__name__}: {e}")
        return {
            "status": "unreachable",
            "connected": False,
        }


@app.post("/run-pipeline/{period}")
async def trigger_pipeline(period: str):
    """Trigger the pipeline manually for testing (morning, midday, afternoon, evening)."""
    if period not in ("morning", "midday", "afternoon", "evening"):
        return JSONResponse(status_code=400, content={"error": "period must be 'morning', 'midday', 'afternoon' or 'evening'"})
    
    pipeline_map = {
        "morning":   run_morning_pipeline,
        "midday":    run_midday_pipeline,
        "afternoon": run_afternoon_pipeline,
        "evening":   run_evening_pipeline,
    }
    asyncio.create_task(pipeline_map[period]())

    return {"status": "started", "period": period, "message": "Pipeline iniciado! Acompanhe os logs com: docker compose logs newsbot -f"}


@app.post("/api/retry-delivery/today")
async def retry_today_delivery():
    from sqlalchemy import select

    from db.engine import async_session
    from db.models import Subscriber, Summary
    from delivery.whatsapp_sender import send_digest

    today = datetime.now(timezone.utc).date()

    async with async_session() as session:
        summaries_result = await session.execute(
            select(Summary)
            .where(
                Summary.date == today,
                Summary.sent_at.is_(None),
            )
            .order_by(Summary.created_at.asc())
        )
        summaries = summaries_result.scalars().all()

        subscribers_result = await session.execute(
            select(Subscriber).where(Subscriber.active == True)  # noqa: E712
        )
        subscribers = subscribers_result.scalars().all()

    if not summaries:
        return {"status": "noop", "message": "Nenhum resumo pendente para hoje."}

    if not subscribers:
        return {"status": "noop", "message": "Nenhum assinante ativo para reenviar."}

    summaries_by_period: dict[str, list[Summary]] = {}
    for summary in summaries:
        summaries_by_period.setdefault(summary.period, []).append(summary)

    sent_count = 0
    for period, period_summaries in summaries_by_period.items():
        sent_count += await send_digest(subscribers, period_summaries, period)

    return {
        "status": "completed",
        "sentSubscribers": sent_count,
        "summaryCount": len(summaries),
        "periods": sorted(summaries_by_period.keys()),
    }


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    """Handle incoming WhatsApp messages from whatsapp-bridge webhook.
    
    Requires header: Authorization: Bearer <WHATSAPP_BRIDGE_TOKEN>
    
    Expected payload:
    {
        "key": {"remoteJid": "551234567890@s.whatsapp.net"},
        "message": {"conversation": "Hello!"}
    }
    
    Returns:
    - 401: Missing or invalid Authorization header
    - 403: Invalid token
    - 400: Invalid payload structure
    - 422: Validation error (missing fields)
    - 200: Successfully queued for processing
    """
    from interactions.webhook_handler import handle_incoming_message
    from pydantic import ValidationError

    # Validate Authorization token (CRITICAL SECURITY FIX)
    auth_header = request.headers.get("Authorization", "")
    # region agent log
    _agent_debug_log(
        "app.py:whatsapp_webhook",
        "auth_header_shape",
        "H1",
        {
            "has_authorization_key": "Authorization" in request.headers,
            "starts_with_bearer": auth_header.startswith("Bearer "),
            "header_len": len(auth_header),
        },
    )
    # endregion
    if not auth_header.startswith("Bearer "):
        logger.warning("Webhook called without valid Authorization header")
        # region agent log
        _agent_debug_log(
            "app.py:whatsapp_webhook",
            "exit_401_missing_or_malformed_bearer",
            "H1",
            {},
        )
        # endregion
        return JSONResponse(
            status_code=401,
            content={"error": "Unauthorized - missing or invalid Authorization header"}
        )

    token = auth_header.replace("Bearer ", "")
    if not settings.whatsapp_bridge_token:
        logger.error("WHATSAPP_BRIDGE_TOKEN not configured in settings")
        # region agent log
        _agent_debug_log(
            "app.py:whatsapp_webhook",
            "exit_500_server_token_unconfigured",
            "H2",
            {},
        )
        # endregion
        return JSONResponse(
            status_code=500,
            content={"error": "Server misconfigured - no webhook token"}
        )

    if not secrets.compare_digest(token, settings.whatsapp_bridge_token):
        logger.warning("Webhook called with invalid token")
        # region agent log
        _agent_debug_log(
            "app.py:whatsapp_webhook",
            "exit_403_token_mismatch",
            "H3",
            {"incoming_token_len": len(token)},
        )
        # endregion
        return JSONResponse(
            status_code=403,
            content={"error": "Forbidden - invalid token"}
        )

    # region agent log
    _agent_debug_log(
        "app.py:whatsapp_webhook",
        "passed_bearer_and_token_digest",
        "H2",
        {"server_token_configured": True},
    )
    # endregion

    try:
        data = await request.json()
    except Exception as e:
        logger.warning(f"Failed to parse webhook JSON: {type(e).__name__}: {e}")
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid JSON"}
        )

    # Validate payload with Pydantic
    try:
        payload = WhatsAppWebhookPayload(**data)
    except ValidationError as e:
        logger.warning(f"Webhook validation error: {e}")
        return JSONResponse(
            status_code=422,
            content={
                "error": "Validation error",
                "details": [err["msg"] for err in e.errors()]
            }
        )

    message_text = payload.message.conversation.strip()
    remote_jid = payload.key.remoteJid.strip()

    if not message_text or not remote_jid:
        logger.warning("Webhook received with empty message or jid")
        return JSONResponse(
            status_code=400,
            content={"error": "Message and remoteJid cannot be empty"}
        )

    # Extract phone number from remoteJid (handle both user and group JIDs)
    from_number = remote_jid.replace("@s.whatsapp.net", "").replace("@lid", "").replace("@g.us", "").strip()
    
    # Check whitelist (supports both phone numbers and group JIDs)
    if settings.allowed_numbers:
        allowed_list = [n.strip() for n in settings.allowed_numbers.split(",")]
        # Check if it's a group message
        is_group = "@g.us" in payload.key.remoteJid
        if is_group:
            # For groups, use full JID
            if payload.key.remoteJid not in allowed_list:
                logger.info(f"Ignoring message from non-whitelisted group: {payload.key.remoteJid}")
                # region agent log
                _agent_debug_log(
                    "app.py:whatsapp_webhook",
                    "exit_200_whitelist_ignored_group",
                    "H4",
                    {"is_group": True},
                )
                # endregion
                return JSONResponse(status_code=200, content={"status": "ignored", "reason": "not_allowed"})
        else:
            # For users, check both plain number and full JID (handles @lid, @s.whatsapp.net formats)
            if from_number not in allowed_list and payload.key.remoteJid not in allowed_list:
                logger.info(f"Ignoring message from non-whitelisted number: {from_number} (JID: {payload.key.remoteJid})")
                # region agent log
                _agent_debug_log(
                    "app.py:whatsapp_webhook",
                    "exit_200_whitelist_ignored_user",
                    "H4",
                    {"is_group": False},
                )
                # endregion
                return JSONResponse(status_code=200, content={"status": "ignored", "reason": "not_allowed"})

    logger.info(f"Webhook received from {remote_jid}: {message_text[:50]}")
    
    # Queue message handling - use ensure_future to keep reference
    task = asyncio.create_task(handle_incoming_message(remote_jid, message_text))
    task.add_done_callback(lambda t: logger.info(f"Message processed for {remote_jid}") if not t.exception() else logger.error(f"Task failed: {t.exception()}"))

    # region agent log
    _agent_debug_log(
        "app.py:whatsapp_webhook",
        "exit_200_queued",
        "VERIFY",
        {"jid_suffix": remote_jid.split("@")[-1] if "@" in remote_jid else "unknown"},
    )
    # endregion
    return JSONResponse(status_code=200, content={"status": "queued"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level=settings.log_level.lower(),
    )
