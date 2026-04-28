from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from config.time_utils import day_bounds_utc, local_now, local_today, to_local, utc_now
from db.models import DeliveryLog, FeedSource, NewsArticle, PipelineRun, Subscriber, Summary
from processor.summary_format import display_category, display_period, normalize_takeaways

PERIOD_SEQUENCE = ["morning", "midday", "afternoon", "evening"]


def _normalize_source_ids(raw_ids) -> list[int]:
    if not isinstance(raw_ids, list):
        return []
    return [article_id for article_id in raw_ids if isinstance(article_id, int)]


def _ordered_source_urls(source_ids: list[int], source_url_map: dict[int, str]) -> list[str]:
    ordered_urls: list[str] = []
    seen_urls: set[str] = set()
    for article_id in source_ids:
        url = source_url_map.get(article_id)
        if url and url not in seen_urls:
            ordered_urls.append(url)
            seen_urls.add(url)
    return ordered_urls


def _summaries_by_category(cards: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for card in cards:
        counts[card["category"]] = counts.get(card["category"], 0) + 1
    return counts


def _recent_run_payload(run: PipelineRun) -> dict:
    started_local = to_local(run.started_at)
    finished_local = to_local(run.finished_at)
    duration_seconds = None
    if run.started_at and run.finished_at:
        duration_seconds = int((run.finished_at - run.started_at).total_seconds())

    return {
        "id": run.id,
        "period": run.period,
        "periodLabel": display_period(run.period),
        "status": run.status,
        "articlesCollected": run.articles_collected,
        "summariesGenerated": run.summaries_generated,
        "messagesSent": run.messages_sent,
        "startedAt": run.started_at.isoformat() if run.started_at else None,
        "finishedAt": run.finished_at.isoformat() if run.finished_at else None,
        "startedAtLabel": started_local.strftime("%H:%M") if started_local else "--",
        "finishedAtLabel": finished_local.strftime("%H:%M") if finished_local else "--",
        "durationSeconds": duration_seconds,
        "errorSnippet": (run.error_log or "")[:180],
    }


def _next_window_payload() -> dict:
    now_local = local_now()
    hours = settings.pipeline_hours_list

    for period, hour in zip(PERIOD_SEQUENCE, hours):
        scheduled = now_local.replace(hour=hour, minute=0, second=0, microsecond=0)
        if scheduled > now_local:
            return {
                "period": period,
                "periodLabel": display_period(period),
                "scheduledAt": scheduled.isoformat(),
                "timeLabel": f"{hour:02d}:00",
                "isTomorrow": False,
            }

    first_hour = hours[0]
    tomorrow = now_local + timedelta(days=1)
    scheduled = tomorrow.replace(hour=first_hour, minute=0, second=0, microsecond=0)
    return {
        "period": PERIOD_SEQUENCE[0],
        "periodLabel": display_period(PERIOD_SEQUENCE[0]),
        "scheduledAt": scheduled.isoformat(),
        "timeLabel": f"{first_hour:02d}:00",
        "isTomorrow": True,
    }


def _timeline_payload(today_runs: list[PipelineRun]) -> list[dict]:
    now_local = local_now()
    run_map: dict[str, PipelineRun] = {}
    for run in today_runs:
        if run.period not in run_map:
            run_map[run.period] = run

    timeline = []
    for period, hour in zip(PERIOD_SEQUENCE, settings.pipeline_hours_list):
        scheduled_local = datetime.combine(local_today(), datetime.min.time(), tzinfo=now_local.tzinfo).replace(
            hour=hour
        )
        run = run_map.get(period)
        if run:
            status = run.status
            details = _recent_run_payload(run)
        else:
            status = "upcoming" if scheduled_local > now_local else "pending"
            details = None

        timeline.append(
            {
                "period": period,
                "periodLabel": display_period(period),
                "scheduledAt": scheduled_local.isoformat(),
                "timeLabel": f"{hour:02d}:00",
                "status": status,
                "details": details,
                "isCurrentWindow": scheduled_local.hour == now_local.hour,
            }
        )
    return timeline


def _health_score(
    *,
    bridge_connected: bool,
    subscriber_count: int,
    pending_summaries: int,
    failed_deliveries: int,
    inactive_feeds: int,
) -> int:
    score = 100
    if not bridge_connected:
        score -= 30
    if subscriber_count == 0:
        score -= 10
    score -= min(20, pending_summaries * 4)
    score -= min(20, failed_deliveries * 5)
    score -= min(20, inactive_feeds * 5)
    return max(0, min(100, score))


def _summary_card(summary: Summary, source_url_map: dict[int, str]) -> dict:
    source_ids = _normalize_source_ids(getattr(summary, "source_article_ids", []))
    source_urls = _ordered_source_urls(source_ids, source_url_map)
    takeaways = normalize_takeaways(
        summary.key_takeaways,
        summary_text=summary.summary_text or "",
        category=summary.category,
        period=summary.period,
    )

    created_local = to_local(summary.created_at)
    sent_local = to_local(summary.sent_at)
    return {
        "id": summary.id,
        "category": summary.category,
        "categoryLabel": display_category(summary.category),
        "period": summary.period,
        "periodLabel": display_period(summary.period),
        "header": takeaways["header"],
        "date": summary.date.isoformat() if summary.date else None,
        "createdAt": summary.created_at.isoformat() if summary.created_at else None,
        "createdAtLabel": created_local.strftime("%H:%M") if created_local else "--",
        "sentAt": summary.sent_at.isoformat() if summary.sent_at else None,
        "sentAtLabel": sent_local.strftime("%H:%M") if sent_local else None,
        "isPending": summary.sent_at is None,
        "hasInsight": bool(takeaways["insight"]),
        "sourceCount": len(source_urls),
        "sourceUrls": source_urls,
        "modelUsed": summary.model_used,
        "bullets": takeaways["bullets"],
        "insight": takeaways["insight"],
        "bodySections": takeaways["sections"],
        "summaryText": summary.summary_text or "",
    }


async def build_dashboard_payload(session: AsyncSession, bridge_status: dict) -> dict:
    today = local_today()
    day_start_utc, day_end_utc = day_bounds_utc(today)

    subscriber_count = await session.scalar(
        select(func.count(Subscriber.id)).where(Subscriber.active.is_(True))
    ) or 0

    reading_since = today - timedelta(days=6)

    summaries_result = await session.execute(
        select(Summary)
        .where(Summary.date >= reading_since)
        .order_by(Summary.created_at.desc())
    )
    summaries = summaries_result.scalars().all()

    pending_summaries = await session.scalar(
        select(func.count(Summary.id)).where(
            Summary.date == today,
            Summary.sent_at.is_(None),
        )
    ) or 0

    failed_deliveries = await session.scalar(
        select(func.count(DeliveryLog.id)).where(
            DeliveryLog.status == "failed",
            DeliveryLog.sent_at >= day_start_utc,
            DeliveryLog.sent_at <= day_end_utc,
        )
    ) or 0

    inactive_feeds_result = await session.execute(
        select(FeedSource).where(FeedSource.active.is_(False)).order_by(FeedSource.name.asc())
    )
    inactive_feeds = inactive_feeds_result.scalars().all()

    recent_runs_result = await session.execute(
        select(PipelineRun).order_by(PipelineRun.started_at.desc()).limit(8)
    )
    recent_runs = recent_runs_result.scalars().all()

    today_runs_result = await session.execute(
        select(PipelineRun)
        .where(PipelineRun.date == today)
        .order_by(PipelineRun.started_at.desc())
    )
    today_runs = today_runs_result.scalars().all()

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
        source_url_map = {article_id: url for article_id, url in source_rows_result.all()}

    cards = [_summary_card(summary, source_url_map) for summary in summaries]
    schedule = [
        {
            "period": period,
            "periodLabel": display_period(period),
            "hour": hour,
            "timeLabel": f"{hour:02d}:00",
        }
        for period, hour in zip(PERIOD_SEQUENCE, settings.pipeline_hours_list)
    ]

    operation = {
        "timezone": settings.timezone,
        "subscriberCount": subscriber_count,
        "todaySummaryCount": len(summaries),
        "pendingSummaryCount": pending_summaries,
        "failedDeliveryCount": failed_deliveries,
        "inactiveFeedCount": len(inactive_feeds),
        "inactiveFeeds": [
            {
                "id": feed.id,
                "name": feed.name,
                "category": feed.category,
                "lastError": (feed.last_error or "")[:140],
            }
            for feed in inactive_feeds
        ],
        "bridge": bridge_status,
        "healthScore": _health_score(
            bridge_connected=bool(bridge_status.get("connected")),
            subscriber_count=subscriber_count,
            pending_summaries=pending_summaries,
            failed_deliveries=failed_deliveries,
            inactive_feeds=len(inactive_feeds),
        ),
        "schedule": schedule,
        "nextWindow": _next_window_payload(),
        "timeline": _timeline_payload(today_runs),
        "recentRuns": [_recent_run_payload(run) for run in recent_runs],
        "lastUpdatedAt": utc_now().isoformat(),
    }

    reading = {
        "summaryCount": len(cards),
        "categoryCounts": _summaries_by_category(cards),
        "cards": cards,
    }

    return {
        "generatedAt": utc_now().isoformat(),
        "timezone": settings.timezone,
        "operation": operation,
        "reading": reading,
    }
