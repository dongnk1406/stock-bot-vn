from __future__ import annotations
import asyncio
import logging
from urllib.parse import quote
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


async def _get(url: str) -> str | None:
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(headers=HEADERS, timeout=15.0, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.text
        except Exception as e:
            logger.warning(f"HTTP attempt {attempt + 1} failed for {url}: {e}")
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
    logger.error(f"All retries failed: {url}")
    return None


def _parse_articles(html: str, source: str, limit: int) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    seen = set()
    items = soup.select(
        ".box-category-item, div.item:has(h3.titlehidden), "
        ".tlitem, .article-item, .story, .item-news, .list-news li"
    )
    for item in items:
        if len(articles) >= limit:
            break
        title_tag = item.select_one(
            "h3.titlehidden a, a.box-category-link-title, h3 a, h2 a, .title a"
        )
        if not title_tag:
            continue
        title = title_tag.get_text(" ", strip=True)
        if not title:
            continue
        link = title_tag.get("href", "")
        if link and not link.startswith("http"):
            link = f"https://cafef.vn{link}"
        if link in seen:
            continue
        seen.add(link)
        summary_tag = item.select_one(".sapo, .summary, p")
        summary = summary_tag.get_text(" ", strip=True)[:300] if summary_tag else ""
        articles.append({"title": title, "summary": summary, "url": link, "source": source})
    return articles


async def fetch_ticker_news(ticker: str, limit: int = 5) -> list[dict]:
    html = await _get(f"https://cafef.vn/tim-kiem.chn?keywords={quote(ticker, safe='')}")
    if not html:
        return []
    articles = _parse_articles(html, "CafeF", limit)
    if not articles:
        logger.warning(f"CafeF: no articles for {ticker} — selectors may need updating")
    return articles


async def fetch_macro_news(limit: int = 5) -> list[dict]:
    html = await _get("https://cafef.vn/vi-mo-dau-tu.chn")
    if not html:
        return []
    return _parse_articles(html, "CafeF Vĩ mô", limit)
