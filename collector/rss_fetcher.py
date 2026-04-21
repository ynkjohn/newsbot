import datetime
import hashlib

import structlog
import feedparser
import httpx
from sqlalchemy import select

from db.engine import async_session
from db.models import FeedSource, NewsArticle

logger = structlog.get_logger()


async def fetch_all_feeds(hours: int = 12) -> list[dict]:
    """Fetch all active feeds and return raw article dicts."""
    async with async_session() as session:
        result = await session.execute(
            select(FeedSource).where(FeedSource.active == True)  # noqa: E712
        )
        sources = result.scalars().all()

    all_entries = []
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for source in sources:
            try:
                resp = await client.get(source.url)
                resp.raise_for_status()
                feed = feedparser.parse(resp.text)

                for entry in feed.entries:
                    published = _parse_published(entry, cutoff)
                    if published and published < cutoff:
                        continue

                    all_entries.append({
                        "source_id": source.id,
                        "source_name": source.name,
                        "category": source.category,
                        "url": entry.get("link", ""),
                        "title": entry.get("title", ""),
                        "description": entry.get("summary", ""),
                        "published_at": published or datetime.datetime.now(datetime.timezone.utc),
                    })

                # Update source fetch timestamp
                async with async_session() as session:
                    src = await session.get(FeedSource, source.id)
                    if src:
                        src.last_fetched_at = datetime.datetime.now(datetime.timezone.utc)
                        src.consecutive_errors = 0
                        await session.commit()

            except Exception as e:
                logger.error(f"Failed to fetch {source.name} ({source.url}): {e}")
                async with async_session() as session:
                    src = await session.get(FeedSource, source.id)
                    if src:
                        src.last_error = str(e)
                        src.consecutive_errors = (src.consecutive_errors or 0) + 1
                        if src.consecutive_errors >= 3:
                            src.active = False
                            logger.warning(f"Disabled feed {source.name} after 3 consecutive errors")
                        await session.commit()

    return all_entries


def _parse_published(entry, cutoff) -> datetime.datetime | None:
    """Try to parse the published date from a feed entry."""
    for field in ("published_parsed", "updated_parsed"):
        parsed = entry.get(field)
        if parsed:
            try:
                dt = datetime.datetime(*parsed[:6], tzinfo=datetime.timezone.utc)
                return dt
            except Exception:
                continue
    return None


def compute_content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
