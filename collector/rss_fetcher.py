import asyncio
import datetime
import hashlib

import feedparser
import httpx
import structlog
from sqlalchemy import select

from config.time_utils import utc_now
from db.engine import async_session
from db.models import FeedSource

logger = structlog.get_logger()
MAX_CONCURRENT_FEEDS = 6


async def fetch_all_feeds(hours: int = 12) -> list[dict]:
    async with async_session() as session:
        result = await session.execute(select(FeedSource).where(FeedSource.active.is_(True)))
        sources = result.scalars().all()

    cutoff = utc_now() - datetime.timedelta(hours=hours)
    all_entries: list[dict] = []
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_FEEDS)

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        tasks = [asyncio.create_task(_fetch_source_entries(client, source, cutoff, semaphore)) for source in sources]
        for task in asyncio.as_completed(tasks):
            all_entries.extend(await task)

    return all_entries


async def _fetch_source_entries(
    client: httpx.AsyncClient,
    source: FeedSource,
    cutoff: datetime.datetime,
    semaphore: asyncio.Semaphore,
) -> list[dict]:
    async with semaphore:
        try:
            response = await client.get(source.url)
            response.raise_for_status()
            feed = feedparser.parse(response.text)

            entries: list[dict] = []
            for entry in feed.entries:
                published = _parse_published(entry)
                if published and published < cutoff:
                    continue

                entries.append(
                    {
                        "source_id": source.id,
                        "source_name": source.name,
                        "category": source.category,
                        "url": entry.get("link", ""),
                        "title": entry.get("title", ""),
                        "description": entry.get("summary", ""),
                        "published_at": published or utc_now(),
                    }
                )

            await _mark_feed_success(source.id)
            return entries
        except Exception as exc:
            logger.error(f"Failed to fetch {source.name} ({source.url}): {exc}")
            await _mark_feed_failure(source.id, str(exc), source.name)
            return []


def _parse_published(entry) -> datetime.datetime | None:
    for field in ("published_parsed", "updated_parsed"):
        parsed = entry.get(field)
        if parsed:
            try:
                return datetime.datetime(*parsed[:6], tzinfo=datetime.timezone.utc)
            except Exception:
                continue
    return None


async def _mark_feed_success(source_id: int) -> None:
    async with async_session() as session:
        source = await session.get(FeedSource, source_id)
        if source:
            source.last_fetched_at = utc_now()
            source.consecutive_errors = 0
            source.last_error = None
            await session.commit()


async def _mark_feed_failure(source_id: int, error_message: str, source_name: str) -> None:
    async with async_session() as session:
        source = await session.get(FeedSource, source_id)
        if not source:
            return

        source.last_error = error_message
        source.consecutive_errors = (source.consecutive_errors or 0) + 1
        if source.consecutive_errors >= 3:
            source.active = False
            logger.warning(f"Disabled feed {source_name} after 3 consecutive errors")
        await session.commit()


def compute_content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
