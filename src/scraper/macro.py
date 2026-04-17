from __future__ import annotations
import asyncio
import logging
import feedparser
import yfinance as yf

logger = logging.getLogger(__name__)

RSS_FEEDS = [
    "https://www.investing.com/rss/news_25.rss",
    "https://www.dailyfx.com/feeds/all",
]

MACRO_SYMBOLS = {
    "DXY": "^DXY",
    "S&P500": "^GSPC",
    "GOLD": "GC=F",
    "OIL": "CL=F",
}


async def fetch_global_macro() -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_macro_sync)


def _fetch_macro_sync() -> dict:
    result = {}
    for name, symbol in MACRO_SYMBOLS.items():
        try:
            hist = yf.Ticker(symbol).history(period="2d")
            if len(hist) >= 2:
                prev = float(hist["Close"].iloc[-2])
                last = float(hist["Close"].iloc[-1])
                result[name] = {
                    "price": round(last, 2),
                    "change_pct": round((last - prev) / prev * 100, 2),
                }
        except Exception as e:
            logger.warning(f"yfinance error {symbol}: {e}")
    return result


async def fetch_rss_news(limit: int = 5) -> list[dict]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_rss_sync, limit)


def _fetch_rss_sync(limit: int) -> list[dict]:
    articles = []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:limit]:
                articles.append({
                    "title": entry.get("title", ""),
                    "summary": entry.get("summary", "")[:300],
                    "url": entry.get("link", ""),
                    "source": feed.feed.get("title", "RSS"),
                })
        except Exception as e:
            logger.warning(f"RSS error {url}: {e}")
    return articles[:limit]
