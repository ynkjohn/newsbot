"""Admin API routes — subscribers, feeds, analytics, summaries, LLM config, retry delivery."""

from typing import Any

import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from config.time_utils import local_today
from db.engine import async_session
from interactions.admin_auth import require_admin
from interactions.messages import retry_no_pending, retry_no_subscribers
from processor.llm_client import reset_llm_client, test_llm_config
from processor.llm_config import LLMConfigError, get_llm_config_store, public_payload

logger = structlog.get_logger()

router = APIRouter()


# --- LLM Config ---


@router.get("/api/llm-config", dependencies=[Depends(require_admin)])
async def get_llm_config():
    return get_llm_config_store().public_payload()


@router.post("/api/llm-config", dependencies=[Depends(require_admin)])
async def save_llm_config(payload: dict[str, Any]):
    try:
        config = get_llm_config_store().save(payload)
    except LLMConfigError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    reset_llm_client()
    return public_payload(config)


@router.post("/api/llm-config/test", dependencies=[Depends(require_admin)])
async def test_llm_config_endpoint(payload: dict[str, Any]):
    try:
        config = get_llm_config_store().build_unsaved(payload)
        await test_llm_config(config)
    except LLMConfigError as exc:
        logger.warning("llm_config_test_error", error=str(exc), payload=payload)
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except Exception as exc:
        logger.warning("llm_config_test_failed", error=str(exc))
        return JSONResponse(status_code=502, content={"ok": False, "error": str(exc)})

    return {"ok": True, "message": "Conexão LLM testada com sucesso."}


# --- Retry Delivery ---


@router.post("/api/retry-delivery/today", dependencies=[Depends(require_admin)])
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


# --- Subscribers ---


@router.get("/api/subscribers", dependencies=[Depends(require_admin)])
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


@router.post("/api/subscribers/{subscriber_id}/toggle", dependencies=[Depends(require_admin)])
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


# --- Feeds ---


@router.get("/api/feeds", dependencies=[Depends(require_admin)])
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


@router.post("/api/feeds/{feed_id}/toggle", dependencies=[Depends(require_admin)])
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


# --- Analytics ---


@router.get("/api/analytics", dependencies=[Depends(require_admin)])
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


# --- Summary Approval ---


@router.post("/api/summaries/{summary_id}/approve", dependencies=[Depends(require_admin)])
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
