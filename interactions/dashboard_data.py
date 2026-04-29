from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from config.time_utils import day_bounds_utc, local_now, local_today, to_local, utc_now
from db.models import DeliveryLog, FeedSource, NewsArticle, PipelineEvent, PipelineRun, Subscriber, Summary
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


def _pipeline_event_payload(event: PipelineEvent) -> dict:
    created_local = to_local(event.created_at)
    return {
        "id": event.id,
        "runId": event.run_id,
        "step": event.step,
        "status": event.status,
        "message": event.message,
        "metadata": event.event_metadata or {},
        "createdAt": event.created_at.isoformat() if event.created_at else None,
        "createdAtLabel": created_local.strftime("%H:%M:%S") if created_local else "--",
    }



def _recent_run_payload(run: PipelineRun, events_by_run: dict[int, list[PipelineEvent]] | None = None) -> dict:
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
        "events": [_pipeline_event_payload(event) for event in (events_by_run or {}).get(run.id, [])],
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
            details = _recent_run_payload(run, None)
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


def _feed_health_payload(feed: FeedSource, now: datetime.datetime) -> dict:
    minutes_since_fetch = None
    if feed.last_fetched_at:
        last_fetched_at = feed.last_fetched_at
        if last_fetched_at.tzinfo is None:
            last_fetched_at = last_fetched_at.replace(tzinfo=now.tzinfo)
        minutes_since_fetch = max(0, int((now - last_fetched_at).total_seconds() // 60))

    active = feed.active if isinstance(feed.active, bool) else True
    consecutive_errors = feed.consecutive_errors if isinstance(feed.consecutive_errors, int) else 0
    fetch_interval = feed.fetch_interval_minutes if isinstance(feed.fetch_interval_minutes, int) else 60
    stale_after = max(fetch_interval * 3, 180)
    if not active:
        state = "paused"
        score = 0
    elif consecutive_errors >= 3:
        state = "broken"
        score = 20
    elif consecutive_errors > 0:
        state = "degraded"
        score = max(30, 85 - consecutive_errors * 15)
    elif minutes_since_fetch is not None and minutes_since_fetch > stale_after:
        state = "stale"
        score = 55
    else:
        state = "healthy"
        score = 100

    return {
        "id": feed.id,
        "name": feed.name,
        "category": feed.category,
        "active": active,
        "state": state,
        "healthScore": score,
        "consecutiveErrors": consecutive_errors,
        "lastError": feed.last_error,
        "lastFetchedAt": feed.last_fetched_at.isoformat() if feed.last_fetched_at else None,
        "minutesSinceFetch": minutes_since_fetch,
    }


def _health_breakdown(bridge_status: dict, pending_count: int, failed_deliveries: int, inactive_feeds: int) -> list[dict]:
    items = []
    if bridge_status.get("status") == "connected" or bridge_status.get("connected") is True:
        items.append({"label": "Bridge online", "impact": 0})
    else:
        items.append({"label": "Bridge offline ou instável", "impact": -30})
    if pending_count:
        items.append({"label": f"{pending_count} resumos pendentes", "impact": -min(20, pending_count * 2)})
    if failed_deliveries:
        items.append({"label": f"{failed_deliveries} entregas falharam", "impact": -min(20, failed_deliveries * 5)})
    if inactive_feeds:
        items.append({"label": f"{inactive_feeds} fontes inativas", "impact": -min(20, inactive_feeds * 5)})
    if len(items) == 1 and items[0]["impact"] == 0:
        items.append({"label": "Sem penalidades operacionais", "impact": 0})
    return items


def _summary_card(summary: Summary, source_url_map: dict[int, str]) -> dict:
    source_ids = _normalize_source_ids(getattr(summary, "source_article_ids", []))
    source_urls = _ordered_source_urls(source_ids, source_url_map)
    raw_takeaways = summary.key_takeaways if isinstance(summary.key_takeaways, dict) else {}
    takeaways = normalize_takeaways(
        summary.key_takeaways,
        summary_text=summary.summary_text or "",
        category=summary.category,
        period=summary.period,
    )
    items = takeaways.get("items") if isinstance(takeaways.get("items"), list) else []
    approval_status = raw_takeaways.get("approval_status") or raw_takeaways.get("approvalStatus") or ""
    sentiment = raw_takeaways.get("sentiment") or "neutral"
    risk_level = raw_takeaways.get("risk_level") or raw_takeaways.get("riskLevel") or "normal"

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
        "approvalStatus": approval_status,
        "sentiment": sentiment,
        "riskLevel": risk_level,
        "items": items,
        "bodySections": takeaways["sections"],
        "summaryText": summary.summary_text or "",
    }


async def build_dashboard_payload(session: AsyncSession, bridge_status: dict) -> dict:
    today = local_today()
    now = utc_now()
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

    failed_delivery_filter = (
        DeliveryLog.status == "failed",
        DeliveryLog.sent_at >= day_start_utc,
        DeliveryLog.sent_at <= day_end_utc,
    )
    failed_deliveries = await session.scalar(
        select(func.count(DeliveryLog.id)).where(*failed_delivery_filter)
    ) or 0
    failed_delivery_rows = await session.execute(
        select(DeliveryLog, Subscriber, Summary)
        .join(Subscriber, DeliveryLog.subscriber_id == Subscriber.id)
        .join(Summary, DeliveryLog.summary_id == Summary.id)
        .where(*failed_delivery_filter)
        .order_by(DeliveryLog.sent_at.desc())
        .limit(20)
    )
    failed_delivery_items = [
        {
            "id": log.id,
            "subscriber": subscriber.phone_number,
            "summaryId": summary.id,
            "summaryHeader": f"{summary.category} — {summary.period}",
            "period": summary.period,
            "category": summary.category,
            "errorMessage": log.error_message,
            "sentAt": log.sent_at.isoformat() if log.sent_at else None,
            "retryable": True,
        }
        for log, subscriber, summary in failed_delivery_rows.all()
    ]

    feeds_result = await session.execute(select(FeedSource).order_by(FeedSource.name.asc()))
    feeds = feeds_result.scalars().all()
    inactive_feeds = [feed for feed in feeds if not feed.active]
    feed_health = [_feed_health_payload(feed, now) for feed in feeds]

    recent_runs_result = await session.execute(
        select(PipelineRun).order_by(PipelineRun.started_at.desc()).limit(8)
    )
    recent_runs = recent_runs_result.scalars().all()
    recent_run_ids = [run.id for run in recent_runs]
    events_by_run: dict[int, list[PipelineEvent]] = {}
    if recent_run_ids:
        events_result = await session.execute(
            select(PipelineEvent)
            .where(PipelineEvent.run_id.in_(recent_run_ids))
            .order_by(PipelineEvent.created_at.asc(), PipelineEvent.id.asc())
        )
        for event in events_result.scalars().all():
            events_by_run.setdefault(event.run_id, []).append(event)

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
    today_summary_count = sum(1 for summary in summaries if summary.date == today)
    reading_window_summary_count = len(summaries)
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
        "todaySummaryCount": today_summary_count,
        "readingWindowSummaryCount": reading_window_summary_count,
        "pendingSummaryCount": pending_summaries,
        "failedDeliveryCount": failed_deliveries,
        "failedDeliveries": {
            "count": failed_deliveries,
            "items": failed_delivery_items,
        },
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
        "feedHealth": feed_health,
        "bridge": bridge_status,
        "healthScore": _health_score(
            bridge_connected=bool(bridge_status.get("connected")) or bridge_status.get("status") == "connected",
            subscriber_count=subscriber_count,
            pending_summaries=pending_summaries,
            failed_deliveries=failed_deliveries,
            inactive_feeds=len(inactive_feeds),
        ),
        "healthBreakdown": _health_breakdown(
            bridge_status,
            pending_summaries,
            failed_deliveries,
            len(inactive_feeds),
        ),
        "schedule": schedule,
        "nextWindow": _next_window_payload(),
        "timeline": _timeline_payload(today_runs),
        "recentRuns": [_recent_run_payload(run, events_by_run) for run in recent_runs],
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
