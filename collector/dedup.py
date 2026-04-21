import structlog

from sqlalchemy import select

from db.engine import async_session
from db.models import NewsArticle

logger = structlog.get_logger()


async def deduplicate_articles(entries: list[dict]) -> list[dict]:
    """Filter out articles whose URLs or candidate content hashes already exist."""
    if not entries:
        return []

    urls = [e["url"] for e in entries if e.get("url")]
    content_hashes = [e["content_hash"] for e in entries if e.get("content_hash")]

    async with async_session() as session:
        # Check existing URLs
        result = await session.execute(
            select(NewsArticle.url).where(NewsArticle.url.in_(urls))
        )
        existing_urls = set(result.scalars().all())

        existing_hashes: set[str] = set()
        if content_hashes:
            result = await session.execute(
                select(NewsArticle.content_hash).where(
                    NewsArticle.content_hash.in_(content_hashes)
                )
            )
            existing_hashes = set(result.scalars().all())

    new_entries = []
    skipped_url = 0
    skipped_hash = 0
    for e in entries:
        if e["url"] in existing_urls:
            skipped_url += 1
        elif e.get("content_hash") and e["content_hash"] in existing_hashes:
            skipped_hash += 1
        else:
            new_entries.append(e)

    if skipped_url or skipped_hash:
        logger.info(f"Skipped {skipped_url} duplicate URLs, {skipped_hash} duplicate content hashes")

    return new_entries
