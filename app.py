import asyncio
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import httpx
import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from collector.sources import seed_feeds_if_empty
from config.settings import settings
from config.time_utils import local_today, local_now
from db.engine import async_session, init_db
from interactions.admin_auth import require_admin
from interactions.dashboard_data import build_dashboard_payload
from interactions.messages import retry_no_pending, retry_no_subscribers
from scheduler.jobs import (
    check_feed_health,
    cleanup_old_articles,
    run_afternoon_pipeline,
    run_evening_pipeline,
    run_midday_pipeline,
    run_morning_pipeline,
)

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
)

logger = structlog.get_logger()
scheduler = AsyncIOScheduler(timezone=settings.timezone)
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


class WhatsAppKeyPayload(BaseModel):
    remoteJid: str = Field(..., min_length=1)


class WhatsAppMessagePayload(BaseModel):
    conversation: str = Field(..., min_length=1)


class WhatsAppWebhookPayload(BaseModel):
    key: WhatsAppKeyPayload
    message: WhatsAppMessagePayload

    @field_validator("key", mode="before")
    @classmethod
    def validate_key(cls, value):
        if not isinstance(value, dict):
            raise ValueError("key must be a dict")
        return value

    @field_validator("message", mode="before")
    @classmethod
    def validate_message(cls, value):
        if not isinstance(value, dict):
            raise ValueError("message must be a dict")
        return value


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting NewsBot...")
    await init_db()
    await seed_feeds_if_empty()

    pipeline_jobs = [
        ("morning", run_morning_pipeline),
        ("midday", run_midday_pipeline),
        ("afternoon", run_afternoon_pipeline),
        ("evening", run_evening_pipeline),
    ]
    for hour, (period, func) in zip(settings.pipeline_hours_list, pipeline_jobs):
        scheduler.add_job(
            func,
            CronTrigger(hour=hour, minute=0),
            id=f"{period}_pipeline",
            max_instances=1,
            misfire_grace_time=900,
        )

    scheduler.add_job(cleanup_old_articles, CronTrigger(hour=0, minute=30), id="cleanup", max_instances=1)
    scheduler.add_job(check_feed_health, CronTrigger(hour="*/6"), id="feed_health", max_instances=1)
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="NewsBot", version="0.3.0", lifespan=lifespan)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


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


def _admin_dashboard_response() -> FileResponse:
    return FileResponse(STATIC_DIR / "dashboard.html")


def _allowed_remote_jid(remote_jid: str) -> bool:
    if not settings.allowed_numbers:
        return True

    allowed_list = [item.strip() for item in settings.allowed_numbers.split(",") if item.strip()]
    normalized_number = remote_jid.replace("@s.whatsapp.net", "").replace("@lid", "").replace("@g.us", "").strip()
    if remote_jid.endswith("@g.us"):
        return remote_jid in allowed_list
    return normalized_number in allowed_list or remote_jid in allowed_list


@app.get("/health")
async def health():
    return {"status": "ok", "service": "newsbot"}


@app.get("/", dependencies=[Depends(require_admin)])
async def dashboard_root():
    return _admin_dashboard_response()


@app.get("/dashboard", dependencies=[Depends(require_admin)])
async def dashboard_page():
    return _admin_dashboard_response()


@app.get("/api/dashboard", dependencies=[Depends(require_admin)])
async def dashboard():
    bridge_status = await fetch_whatsapp_status()
    async with async_session() as session:
        return await build_dashboard_payload(session, bridge_status)


@app.get("/api/whatsapp-status", dependencies=[Depends(require_admin)])
async def whatsapp_status():
    return await fetch_whatsapp_status()


@app.get("/api/digest-preview/{period}", dependencies=[Depends(require_admin)])
async def digest_preview(period: str):
    from sqlalchemy import select

    from db.models import Summary
    from delivery.message_formatter import format_digest, split_message

    valid_periods = {"morning", "midday", "afternoon", "evening"}
    if period not in valid_periods:
        return JSONResponse(
            status_code=400,
            content={"error": "period must be 'morning', 'midday', 'afternoon' or 'evening'"},
        )

    today = local_today()
    async with async_session() as session:
        result = await session.execute(
            select(Summary)
            .where(Summary.date == today, Summary.period == period)
            .order_by(Summary.category.asc(), Summary.created_at.asc())
        )
        summaries = result.scalars().all()

    preview_text = format_digest(summaries, today, period)
    parts = split_message(preview_text)
    return {
        "period": period,
        "date": today.isoformat(),
        "generatedAt": local_now().isoformat(),
        "summaryCount": len(summaries),
        "charCount": len(preview_text),
        "partCount": len(parts),
        "text": preview_text,
        "parts": parts,
    }


@app.post("/run-pipeline/{period}", dependencies=[Depends(require_admin)])
async def trigger_pipeline(period: str):
    pipeline_map = {
        "morning": run_morning_pipeline,
        "midday": run_midday_pipeline,
        "afternoon": run_afternoon_pipeline,
        "evening": run_evening_pipeline,
    }
    if period not in pipeline_map:
        return JSONResponse(
            status_code=400,
            content={"error": "period must be 'morning', 'midday', 'afternoon' or 'evening'"},
        )

    run_id = uuid4().hex[:12]
    logger.info(f"Manual pipeline run requested: period={period} run_id={run_id}")
    task = asyncio.create_task(pipeline_map[period](request_id=run_id))

    def log_pipeline_result(done_task: asyncio.Task, requested_period: str = period, requested_run_id: str = run_id) -> None:
        try:
            done_task.result()
        except Exception as exc:
            logger.error(
                f"Manual pipeline run failed: period={requested_period} run_id={requested_run_id} error={type(exc).__name__}: {exc}"
            )
        else:
            logger.info(f"Manual pipeline run finished: period={requested_period} run_id={requested_run_id}")

    task.add_done_callback(log_pipeline_result)
    return {
        "status": "started",
        "period": period,
        "run_id": run_id,
        "message": "Pipeline iniciado. Acompanhe a execução nos logs do serviço newsbot.",
    }


@app.post("/api/retry-delivery/today", dependencies=[Depends(require_admin)])
async def retry_today_delivery():
    from sqlalchemy import select

    from db.models import Subscriber, Summary
    from delivery.whatsapp_sender import send_digest

    today = local_today()
    async with async_session() as session:
        summaries_result = await session.execute(
            select(Summary)
            .where(Summary.date == today, Summary.sent_at.is_(None))
            .order_by(Summary.created_at.asc())
        )
        summaries = summaries_result.scalars().all()

        subscribers_result = await session.execute(select(Subscriber).where(Subscriber.active.is_(True)))
        subscribers = subscribers_result.scalars().all()

    if not summaries:
        return {"status": "noop", "message": retry_no_pending()}
    if not subscribers:
        return {"status": "noop", "message": retry_no_subscribers()}

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


@app.get("/api/subscribers", dependencies=[Depends(require_admin)])
async def list_subscribers():
    from sqlalchemy import select

    from db.models import Subscriber

    async with async_session() as session:
        result = await session.execute(select(Subscriber).order_by(Subscriber.subscribed_at.desc()))
        subscribers = result.scalars().all()

    return [
        {
            "id": sub.id,
            "phoneNumber": sub.phone_number,
            "name": sub.name,
            "active": sub.active,
            "preferences": sub.preferences or {},
            "subscribedAt": sub.subscribed_at.isoformat() if sub.subscribed_at else None,
            "lastSentAt": sub.last_sent_at.isoformat() if sub.last_sent_at else None,
        }
        for sub in subscribers
    ]


@app.post("/api/subscribers/{subscriber_id}/toggle", dependencies=[Depends(require_admin)])
async def toggle_subscriber(subscriber_id: int):
    from sqlalchemy import select

    from db.models import Subscriber

    async with async_session() as session:
        result = await session.execute(select(Subscriber).where(Subscriber.id == subscriber_id))
        subscriber = result.scalar_one_or_none()
        if not subscriber:
            return JSONResponse(status_code=404, content={"error": "Assinante não encontrado."})
        subscriber.active = not subscriber.active
        await session.commit()
        return {"id": subscriber.id, "active": subscriber.active}


@app.get("/api/feeds", dependencies=[Depends(require_admin)])
async def list_feeds():
    from sqlalchemy import select

    from db.models import FeedSource

    async with async_session() as session:
        result = await session.execute(select(FeedSource).order_by(FeedSource.name.asc()))
        feeds = result.scalars().all()

    return [
        {
            "id": feed.id,
            "name": feed.name,
            "url": feed.url,
            "category": feed.category,
            "active": feed.active,
            "consecutive_errors": feed.consecutive_errors,
            "lastError": (feed.last_error or "")[:140],
        }
        for feed in feeds
    ]


@app.post("/api/feeds/{feed_id}/toggle", dependencies=[Depends(require_admin)])
async def toggle_feed(feed_id: int):
    from sqlalchemy import select

    from db.models import FeedSource

    async with async_session() as session:
        result = await session.execute(select(FeedSource).where(FeedSource.id == feed_id))
        feed = result.scalar_one_or_none()
        if not feed:
            return JSONResponse(status_code=404, content={"error": "Fonte não encontrada."})
        feed.active = not feed.active
        await session.commit()
        return {"id": feed.id, "active": feed.active}


@app.get("/api/analytics", dependencies=[Depends(require_admin)])
async def analytics_data():
    from datetime import timedelta

    from sqlalchemy import func, select

    from db.models import NewsArticle, Summary

    today = local_today()
    since = today - timedelta(days=6)

    async with async_session() as session:
        articles_result = await session.execute(
            select(NewsArticle.category, func.count(NewsArticle.id))
            .where(NewsArticle.fetched_at >= since.isoformat())
            .group_by(NewsArticle.category)
        )
        articles_by_category = {row[0]: row[1] for row in articles_result.all()}

        tokens_result = await session.execute(
            select(Summary.date, func.sum(Summary.token_count))
            .where(Summary.date >= since)
            .group_by(Summary.date)
            .order_by(Summary.date.asc())
        )
        tokens_by_date = {
            row[0].strftime("%d/%m") if row[0] else "?": row[1] or 0
            for row in tokens_result.all()
        }

    return {
        "articlesByCategory": articles_by_category,
        "tokensByDate": tokens_by_date,
    }


@app.post("/api/summaries/{summary_id}/approve", dependencies=[Depends(require_admin)])
async def approve_summary(summary_id: int):
    from sqlalchemy import select

    from db.models import Summary

    async with async_session() as session:
        result = await session.execute(select(Summary).where(Summary.id == summary_id))
        summary = result.scalar_one_or_none()
        if not summary:
            return JSONResponse(status_code=404, content={"error": "Resumo não encontrado."})
        kt = summary.key_takeaways or {}
        if isinstance(kt, dict) and kt.get("approval_status") == "draft":
            kt["approval_status"] = "approved"
            summary.key_takeaways = kt
            await session.commit()
        return {"id": summary.id, "status": "approved"}


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    from interactions.webhook_handler import handle_incoming_message
    from pydantic import ValidationError

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

    if not _allowed_remote_jid(remote_jid):
        return JSONResponse(status_code=200, content={"status": "ignored", "reason": "not_allowed"})

    task = asyncio.create_task(handle_incoming_message(remote_jid, message_text))
    task.add_done_callback(
        lambda current_task: logger.error(f"Task failed: {current_task.exception()}")
        if current_task.exception()
        else logger.info(f"Message processed for {remote_jid}")
    )
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
