"""Validate live article extraction quality against active RSS sources.

Usage:
    python validate_extraction.py --limit 8

The report compares RSS summary length, a legacy BeautifulSoup-style extraction,
and the current extraction pipeline for the newest items in the live feed DB.
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import feedparser
import httpx
from bs4 import BeautifulSoup

from collector.article_extractor import _HEADERS, _try_amp_url, extract_article_content


DB_PATH = Path("data/newsbot.db")


@dataclass
class FeedItem:
    source: str
    category: str
    feed_url: str
    article_url: str
    title: str
    rss_summary: str


def _clean_html(html: str) -> str:
    return BeautifulSoup(html or "", "lxml").get_text(separator=" ", strip=True)


def _active_feeds(limit: int) -> list[tuple[str, str, str]]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            select name, category, url
            from feed_sources
            where active = 1
            order by category, name
            limit ?
            """,
            (limit,),
        ).fetchall()
    return [(str(name), str(category), str(url)) for name, category, url in rows]


async def _latest_feed_items(limit: int) -> list[FeedItem]:
    items: list[FeedItem] = []
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers=_HEADERS) as client:
        for source, category, feed_url in _active_feeds(limit):
            try:
                response = await client.get(feed_url)
                response.raise_for_status()
            except Exception as exc:
                print(f"FEED_FAIL source={source!r} url={feed_url} error={exc}")
                continue

            feed = feedparser.parse(response.text)
            if not feed.entries:
                print(f"FEED_EMPTY source={source!r} url={feed_url}")
                continue

            entry = feed.entries[0]
            article_url = getattr(entry, "link", "")
            if not article_url:
                continue
            items.append(
                FeedItem(
                    source=source,
                    category=category,
                    feed_url=feed_url,
                    article_url=article_url,
                    title=getattr(entry, "title", ""),
                    rss_summary=_clean_html(getattr(entry, "summary", "")),
                )
            )
    return items


async def _legacy_bs4_extract(url: str) -> str:
    fetch_url = _try_amp_url(url) or url
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers=_HEADERS) as client:
        response = await client.get(fetch_url)
        response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    for selector in ["article", "[role='main']", ".article-body", ".post-content", ".entry-content"]:
        container = soup.select_one(selector)
        if container:
            paragraphs = container.find_all("p")
            if paragraphs:
                return "\n".join(p.get_text(strip=True) for p in paragraphs)

    return "\n".join(p.get_text(strip=True) for p in soup.find_all("p"))


def _tail(text: str, size: int = 140) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= size:
        return compact
    return "..." + compact[-size:]


async def _validate(limit: int) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    items = await _latest_feed_items(limit)
    if not items:
        print("No feed items found.")
        return 1

    print("source\tcategory\trss_chars\tlegacy_chars\tnew_chars\tgain_vs_rss\tstatus\ttitle")
    failures = 0
    for item in items:
        try:
            legacy = await _legacy_bs4_extract(item.article_url)
        except Exception:
            legacy = ""

        current = await extract_article_content(item.article_url, item.rss_summary)
        rss_len = len(item.rss_summary)
        legacy_len = len(legacy.strip())
        current_len = len(current.strip())
        gain = round(current_len / max(rss_len, 1), 2)
        status = "FULL" if current_len >= 1200 else "CHECK"
        if current_len == 0:
            status = "BLOCKED"
        elif current_len > max(rss_len, legacy_len, 1) * 1.3:
            status = "IMPROVED"
        if status not in {"FULL", "IMPROVED"}:
            failures += 1

        print(
            f"{item.source}\t{item.category}\t{rss_len}\t{legacy_len}\t{current_len}\t"
            f"{gain}\t{status}\t{item.title}"
        )
        print(f"  url: {item.article_url}")
        print(f"  final_text_tail: {_tail(current)}")

    print(f"\nvalidated={len(items)} ok={len(items) - failures} check={failures}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=8, help="number of active feeds to sample")
    args = parser.parse_args()
    return asyncio.run(_validate(args.limit))


if __name__ == "__main__":
    raise SystemExit(main())
