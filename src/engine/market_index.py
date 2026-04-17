from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional
import pandas as pd
from src.config import VN30_TICKERS

logger = logging.getLogger(__name__)

# DNSE / entrade index symbols (see vnstock.technical.ohlc_data docstring).
INDEX_SYMBOLS = {
    "VN-Index": "VNINDEX",
    "VN30": "VN30",
    "HNX-Index": "HNX",
    "UPCOM-Index": "UPCOM",
}


def _fetch_ohlc_sync(symbol: str, type_: str) -> Optional[pd.DataFrame]:
    try:
        from vnstock import stock_historical_data
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        df = stock_historical_data(
            symbol=symbol,
            start_date=start,
            end_date=end,
            resolution="1D",
            type=type_,
            beautify=True,
            source="DNSE",
        )
        if df is None or df.empty:
            return None
        df.columns = [c.lower() for c in df.columns]
        return df.dropna(subset=["close"])
    except Exception as e:
        logger.warning(f"OHLC fetch failed {symbol} ({type_}): {e}")
        return None


async def _get_ohlc(symbol: str, type_: str) -> Optional[pd.DataFrame]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_ohlc_sync, symbol, type_)


async def fetch_index_snapshot() -> dict:
    """Return today's snapshot for major VN indices: OHLC, change %, volume."""
    results: dict = {}
    for name, sym in INDEX_SYMBOLS.items():
        df = await _get_ohlc(sym, "index")
        if df is None or len(df) < 2:
            continue
        try:
            today = df.iloc[-1]
            prev_close = float(df["close"].iloc[-2])
            close = float(today["close"])
            results[name] = {
                "symbol": sym,
                "open": round(float(today["open"]), 2),
                "high": round(float(today["high"]), 2),
                "low": round(float(today["low"]), 2),
                "close": round(close, 2),
                "prev_close": round(prev_close, 2),
                "change_abs": round(close - prev_close, 2),
                "change_pct": round((close - prev_close) / prev_close * 100, 2) if prev_close else 0,
                "volume": float(today.get("volume", 0) or 0),
            }
        except Exception as e:
            logger.warning(f"Index parse error {sym}: {e}")
    return results


def _fetch_top_mover_sync(report_name: str) -> Optional[list[dict]]:
    """Call vnstock.market_top_mover; returns a list of rows or None on failure."""
    try:
        from vnstock import market_top_mover
        df = market_top_mover(report_name=report_name, exchange="All", lang="en")
        if df is None or df.empty:
            return None
        return df.to_dict(orient="records")
    except Exception as e:
        logger.warning(f"market_top_mover {report_name} failed: {e}")
        return None


async def _vn30_changes() -> list[dict]:
    """Fallback: compute today's % change for each VN30 ticker via DNSE."""
    loop = asyncio.get_event_loop()
    tasks = [loop.run_in_executor(None, _fetch_ohlc_sync, t, "stock") for t in VN30_TICKERS]
    dfs = await asyncio.gather(*tasks, return_exceptions=True)
    rows: list[dict] = []
    for ticker, df in zip(VN30_TICKERS, dfs):
        if not isinstance(df, pd.DataFrame) or len(df) < 2:
            continue
        try:
            prev_close = float(df["close"].iloc[-2])
            close = float(df["close"].iloc[-1])
            if prev_close <= 0:
                continue
            rows.append({
                "ticker": ticker,
                "close": round(close, 2),
                "change_pct": round((close - prev_close) / prev_close * 100, 2),
                "volume": float(df["volume"].iloc[-1]),
            })
        except Exception:
            continue
    return rows


async def fetch_top_movers(top_n: int = 5) -> dict:
    """Return top gainers/losers. Tries SSI, falls back to VN30-only scan."""
    loop = asyncio.get_event_loop()
    gainers_raw, losers_raw = await asyncio.gather(
        loop.run_in_executor(None, _fetch_top_mover_sync, "Gainers"),
        loop.run_in_executor(None, _fetch_top_mover_sync, "Losers"),
    )

    def _normalize(rows: Optional[list[dict]]) -> list[dict]:
        if not rows:
            return []
        out = []
        for r in rows[:top_n]:
            ticker = r.get("ticker") or r.get("organCode") or r.get("Symbol") or r.get("symbol")
            pct = (
                r.get("percentPriceChange")
                or r.get("priceChangePercent")
                or r.get("change_pct")
                or r.get("PercentPriceChange")
            )
            price = r.get("matchPrice") or r.get("price") or r.get("Price") or r.get("closePrice")
            if not ticker or pct is None:
                continue
            try:
                out.append({
                    "ticker": str(ticker),
                    "change_pct": round(float(pct), 2),
                    "close": round(float(price), 2) if price is not None else None,
                })
            except (TypeError, ValueError):
                continue
        return out

    gainers = _normalize(gainers_raw)
    losers = _normalize(losers_raw)

    if not gainers or not losers:
        logger.info("Top-mover API unavailable; falling back to VN30 scan.")
        rows = await _vn30_changes()
        if rows:
            gainers = sorted(rows, key=lambda r: r["change_pct"], reverse=True)[:top_n]
            losers = sorted(rows, key=lambda r: r["change_pct"])[:top_n]

    return {"gainers": gainers, "losers": losers}
