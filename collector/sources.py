from sqlalchemy import select

from db.engine import async_session
from db.models import FeedSource

SEED_FEEDS = [
    # Política Brasil
    {"url": "https://g1.globo.com/rss/g1/politica/", "name": "G1 Política", "category": "politica-brasil"},
    {"url": "https://www.metropoles.com/feed", "name": "Metropoles", "category": "politica-brasil"},
    {"url": "https://www.congressoemfoco.com.br/feed/", "name": "Congresso em Foco", "category": "politica-brasil"},
    # Economia Brasil
    {"url": "https://g1.globo.com/rss/g1/economia/", "name": "G1 Economia", "category": "economia-brasil"},
    {"url": "https://www.infomoney.com.br/onde-investir/feed/", "name": "InfoMoney", "category": "economia-brasil"},
    {"url": "https://www.suno.com.br/feed/", "name": "Suno", "category": "economia-brasil"},
    # Cripto
    {"url": "https://www.coindesk.com/arc/outboundfeeds/rss/", "name": "CoinDesk", "category": "economia-cripto"},
    {"url": "https://cointelegraph.com/rss", "name": "Cointelegraph", "category": "economia-cripto"},
    {"url": "https://decrypt.co/feed", "name": "Decrypt", "category": "economia-cripto"},
    # Economia Mundão
    {"url": "https://feeds.bloomberg.com/markets/news.rss", "name": "Bloomberg", "category": "economia-mundao"},
    {"url": "https://www.ft.com/rss/home", "name": "Financial Times", "category": "economia-mundao"},
    {"url": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml", "name": "Wall Street Journal", "category": "economia-mundao"},
    # Política Mundão
    {"url": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "name": "NY Times", "category": "politica-mundao"},
    {"url": "https://www.aljazeera.com/xml/rss/all.xml", "name": "Al Jazeera", "category": "politica-mundao"},
    # Tech
    {"url": "https://g1.globo.com/rss/g1/tecnologia/", "name": "G1 Tecnologia", "category": "tech"},
    {"url": "https://techcrunch.com/feed/", "name": "TechCrunch", "category": "tech"},
    {"url": "https://www.wired.com/feed/rss", "name": "WIRED", "category": "tech"},
    {"url": "https://www.theverge.com/rss/index.xml", "name": "The Verge", "category": "tech"},
    {"url": "https://www.engadget.com/rss.xml", "name": "Engadget", "category": "tech"},
]


async def sync_seed_feeds() -> int:
    """Insert missing seed feeds into the database and return count of inserted ones."""
    async with async_session() as session:
        result = await session.execute(select(FeedSource.url))
        existing_urls = set(result.scalars().all())

        inserted_count = 0
        for feed_data in SEED_FEEDS:
            if feed_data["url"] not in existing_urls:
                source = FeedSource(**feed_data)
                session.add(source)
                inserted_count += 1

        if inserted_count > 0:
            await session.commit()
        return inserted_count


async def seed_feeds_if_empty() -> None:
    """Compatibility wrapper for app.py."""
    await sync_seed_feeds()
