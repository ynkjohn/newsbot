import structlog

import httpx
from bs4 import BeautifulSoup
from newspaper import Article, Config

logger = structlog.get_logger()

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}


async def extract_article_content(url: str, fallback_description: str = "") -> str:
    """Extract full article text from a URL. Falls back through:
    1. newspaper3k
    2. BeautifulSoup
    3. RSS description (always used if scraper returns < 100 chars)
    """
    scraped = ""

    # Try BeautifulSoup first (handles AMP URLs which often work for paywalled sites)
    try:
        scraped = await _extract_with_bs4(url)
        if scraped and len(scraped.strip()) > 100:
            return scraped.strip()
    except Exception as e:
        logger.debug(f"BeautifulSoup failed for {url}: {e}")

    # Try newspaper3k as fallback
    try:
        scraped = await _extract_with_newspaper(url)
        if scraped and len(scraped.strip()) > 100:
            return scraped.strip()
    except Exception as e:
        logger.debug(f"newspaper3k failed for {url}: {e}")

    # Fallback: use RSS description (many sites put good summaries in RSS)
    if fallback_description:
        cleaned = _clean_html(fallback_description).strip()
        if len(cleaned) > 50:
            return cleaned[:3000]

    # If scraper got something but was short, return it anyway
    if scraped and len(scraped.strip()) > 50:
        return scraped.strip()

    return ""


def _clean_html(html: str) -> str:
    """Strip HTML tags from a string."""
    soup = BeautifulSoup(html, "lxml")
    return soup.get_text(separator=" ", strip=True)


async def _extract_with_newspaper(url: str) -> str:
    """Extract using newspaper3k (synchronous library, run in thread)."""
    import asyncio

    def _sync_extract():
        config = Config()
        config.browser_user_agent = _HEADERS["User-Agent"]
        config.request_timeout = 30
        article = Article(url, config=config)
        article.download()
        article.parse()
        return article.text

    return await asyncio.get_event_loop().run_in_executor(None, _sync_extract)


async def _extract_with_bs4(url: str) -> str:
    """Extract article text using BeautifulSoup as fallback."""
    # Try AMP version first for sites that support it (G1, etc.)
    amp_url = _try_amp_url(url)

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers=_HEADERS) as client:
        resp = await client.get(amp_url or url)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")

    # Remove script/style tags
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    # Try common article containers
    for selector in ["article", "[role='main']", ".article-body", ".post-content", ".entry-content"]:
        container = soup.select_one(selector)
        if container:
            paragraphs = container.find_all("p")
            if paragraphs:
                return "\n".join(p.get_text(strip=True) for p in paragraphs)

    # Fallback: all <p> tags
    paragraphs = soup.find_all("p")
    if paragraphs:
        return "\n".join(p.get_text(strip=True) for p in paragraphs)

    return ""


def _try_amp_url(url: str) -> str | None:
    """Convert article URL to AMP version for easier scraping."""
    # G1: .globo.com → .globo.com/amp
    if "g1.globo.com" in url and "/amp" not in url:
        return url + "?outputType=amp"
    return None
