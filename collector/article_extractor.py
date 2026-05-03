import json
import re

import structlog

import httpx
from bs4 import BeautifulSoup
from newspaper import Article, Config
from trafilatura import extract as trafilatura_extract

logger = structlog.get_logger()

_MIN_ARTICLE_CHARS = 220
_MIN_SHORT_ARTICLE_CHARS = 120

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
    "Referer": "https://www.google.com/",
}

_NOISE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"^\s*leia tamb[eé]m\b",
        r"^\s*veja tamb[eé]m\b",
        r"^\s*related\s*:",
        r"^\s*publicidade\s*$",
        r"^\s*continua ap[oó]s a publicidade\s*$",
        r"^\s*assine\b",
        r"^\s*newsletter\b",
    ]
]

_BLOCKER_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"enable javascript",
        r"disable your ad blocker",
        r"sign in to continue",
        r"subscribe to continue",
        r"assine para continuar",
        r"fa[cç]a login para continuar",
    ]
]

_ARTICLE_SELECTORS = [
    "article",
    "[role='main']",
    ".article-body",
    ".post-content",
    ".entry-content",
    ".content-text",
    ".mc-article-body",
    ".materia-conteudo",
    ".texto",
]


async def extract_article_content(url: str, fallback_description: str = "") -> str:
    """Extract full article text from a URL. Falls back through:
    1. trafilatura / JSON-LD / BeautifulSoup from fetched HTML
    2. newspaper3k from fetched HTML
    3. RSS description, only when the article candidates are too thin
    """
    html = ""
    try:
        html = await _fetch_article_html(url)
    except Exception as e:
        logger.debug(f"HTML fetch failed for {url}: {e}")

    candidates = []
    if html:
        for extractor_name, extractor in [
            ("json_ld", lambda: _extract_from_json_ld(html)),
            ("trafilatura", lambda: _extract_with_trafilatura(html, url)),
            ("bs4", lambda: _extract_with_bs4(html)),
            ("newspaper3k", lambda: _extract_with_newspaper_html(html, url)),
        ]:
            try:
                extracted = extractor()
            except Exception as e:
                logger.debug(f"{extractor_name} failed for {url}: {e}")
                continue
            cleaned = _clean_article_text(extracted)
            if cleaned:
                candidates.append(cleaned)

    best = _best_article_candidate(candidates)
    if best:
        return best

    # Last network fallback for unusual pages where newspaper3k can fetch a canonical document.
    try:
        scraped = await _extract_with_newspaper(url)
        cleaned = _clean_article_text(scraped)
        if _is_substantial_article(cleaned):
            return cleaned
    except Exception as e:
        logger.debug(f"newspaper3k failed for {url}: {e}")

    if fallback_description:
        cleaned = _clean_html(fallback_description).strip()
        if len(cleaned) > 50:
            return cleaned[:3000]

    if candidates:
        return max(candidates, key=len)

    return ""


def _clean_html(html: str) -> str:
    """Strip HTML tags from a string."""
    soup = BeautifulSoup(html, "lxml")
    return soup.get_text(separator=" ", strip=True)


async def _fetch_article_html(url: str) -> str:
    """Download article HTML, preferring AMP where it is known to expose plain text."""
    amp_url = _try_amp_url(url)

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers=_HEADERS) as client:
        resp = await client.get(amp_url or url)
        resp.raise_for_status()
        return resp.text


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


def _extract_with_newspaper_html(html: str, url: str) -> str:
    """Extract with newspaper3k without issuing a second request."""
    config = Config()
    config.browser_user_agent = _HEADERS["User-Agent"]
    config.request_timeout = 30
    article = Article(url, config=config)
    article.set_html(html)
    article.parse()
    return article.text


def _extract_with_trafilatura(html: str, url: str) -> str:
    """Extract main text with trafilatura, tuned for recall over summaries."""
    return (
        trafilatura_extract(
            html,
            url=url,
            include_comments=False,
            include_tables=False,
            favor_recall=True,
            no_fallback=False,
        )
        or ""
    )


def _extract_from_json_ld(html: str) -> str:
    """Extract articleBody from schema.org JSON-LD when publishers expose it."""
    soup = BeautifulSoup(html, "lxml")
    bodies = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if not raw:
            continue
        for item in _iter_json_ld_items(raw):
            body = item.get("articleBody") if isinstance(item, dict) else None
            if isinstance(body, str):
                bodies.append(body)
    return "\n\n".join(bodies)


def _iter_json_ld_items(raw: str):
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return

    stack = data if isinstance(data, list) else [data]
    while stack:
        item = stack.pop(0)
        if isinstance(item, dict):
            yield item
            graph = item.get("@graph")
            if isinstance(graph, list):
                stack.extend(graph)
        elif isinstance(item, list):
            stack.extend(item)


def _extract_with_bs4(html: str) -> str:
    """Extract article text using publisher containers and paragraph filtering."""
    soup = BeautifulSoup(html, "lxml")

    # Remove script/style tags
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript"]):
        tag.decompose()

    for selector in _ARTICLE_SELECTORS:
        container = soup.select_one(selector)
        if container:
            text = _paragraph_text(container.find_all("p"))
            if text:
                return text

    return _paragraph_text(soup.find_all("p"))


def _paragraph_text(paragraphs) -> str:
    lines = []
    seen = set()
    for paragraph in paragraphs:
        line = " ".join(paragraph.get_text(" ", strip=True).split())
        if not line or line in seen or _is_noise_line(line):
            continue
        seen.add(line)
        lines.append(line)
    return "\n".join(lines)


def _clean_article_text(text: str | None) -> str:
    if not text:
        return ""
    if not _looks_textual(text):
        return ""

    lines = []
    seen = set()
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if not line or line in seen or _is_noise_line(line):
            continue
        seen.add(line)
        lines.append(line)
    return "\n".join(lines).strip()


def _best_article_candidate(candidates: list[str]) -> str:
    substantial = [candidate for candidate in candidates if _is_substantial_article(candidate)]
    if substantial:
        return max(substantial, key=_article_score)

    return ""


def _is_substantial_article(text: str) -> bool:
    if len(text) < _MIN_SHORT_ARTICLE_CHARS:
        return False
    if not _looks_textual(text):
        return False
    if _looks_like_blocker(text):
        return False

    paragraphs = [line for line in text.splitlines() if len(line) >= 40]
    sentence_count = len(re.findall(r"[.!?](?:\s|$)", text))
    return len(text) >= _MIN_ARTICLE_CHARS or (len(paragraphs) >= 3 and sentence_count >= 3)


def _article_score(text: str) -> tuple[int, int]:
    paragraph_count = len([line for line in text.splitlines() if len(line) >= 40])
    return (paragraph_count, len(text))


def _is_noise_line(line: str) -> bool:
    return any(pattern.search(line) for pattern in _NOISE_PATTERNS)


def _looks_like_blocker(text: str) -> bool:
    matches = sum(1 for pattern in _BLOCKER_PATTERNS if pattern.search(text))
    if matches >= 1 and len(text) < 500:
        return True
    return matches >= 2


def _looks_textual(text: str) -> bool:
    if not text:
        return False
    replacement_count = text.count("\ufffd")
    if replacement_count / max(len(text), 1) > 0.01:
        return False
    control_count = sum(1 for char in text if ord(char) < 32 and char not in "\n\r\t")
    return control_count / max(len(text), 1) <= 0.005


def _try_amp_url(url: str) -> str | None:
    """Convert article URL to AMP version for easier scraping."""
    # G1: .globo.com → .globo.com/amp
    if "g1.globo.com" in url and "/amp" not in url:
        return url + "?outputType=amp"
    return None
