from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional
import pandas as pd
import ta

logger = logging.getLogger(__name__)


def _fetch_sync(ticker: str, days: int, interval: str) -> Optional[pd.DataFrame]:
    try:
        from vnstock import stock_historical_data
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        df = stock_historical_data(
            symbol=ticker,
            start_date=start,
            end_date=end,
            resolution=interval,
            type="stock",
            beautify=True,
            source="DNSE",
        )
        df.columns = [c.lower() for c in df.columns]
        if "time" in df.columns:
            df = df.set_index("time")
        return df.dropna()
    except Exception as e:
        logger.error(f"vnstock error {ticker} ({interval}): {e}")
        return None


async def _get_data(ticker: str, days: int, interval: str) -> Optional[pd.DataFrame]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_sync, ticker, days, interval)


async def compute_daily_signals(ticker: str) -> Optional[dict]:
    df = await _get_data(ticker, days=120, interval="1D")
    if df is None or len(df) < 51:
        logger.warning(f"Not enough daily data for {ticker}")
        return None
    try:
        close = df["close"].astype(float)
        volume = df["volume"].astype(float)

        ma20 = ta.trend.SMAIndicator(close=close, window=20).sma_indicator()
        ma50 = ta.trend.SMAIndicator(close=close, window=50).sma_indicator()
        rsi = ta.momentum.RSIIndicator(close=close, window=14).rsi()
        macd_diff = ta.trend.MACD(close=close).macd_diff()

        crossover_days = None
        bearish_crossover_days = None
        for i in range(1, 5):
            if len(macd_diff) <= i:
                break
            cur, prev = macd_diff.iloc[-i], macd_diff.iloc[-i - 1]
            if crossover_days is None and cur > 0 and prev <= 0:
                crossover_days = i
            if bearish_crossover_days is None and cur < 0 and prev >= 0:
                bearish_crossover_days = i
            if crossover_days is not None and bearish_crossover_days is not None:
                break

        vol_avg20 = float(volume.iloc[-20:].mean())

        return {
            "ticker": ticker,
            "price": round(float(close.iloc[-1]), 2),
            "ma20": round(float(ma20.iloc[-1]), 2),
            "ma50": round(float(ma50.iloc[-1]), 2),
            "rsi": round(float(rsi.iloc[-1]), 2),
            "macd_diff": round(float(macd_diff.iloc[-1]), 4),
            "macd_crossover_days": crossover_days,
            "macd_bearish_crossover_days": bearish_crossover_days,
            "volume_today": float(volume.iloc[-1]),
            "volume_avg20": vol_avg20,
            "volume_ratio": round(float(volume.iloc[-1]) / vol_avg20, 2) if vol_avg20 > 0 else 0,
        }
    except Exception as e:
        logger.error(f"Daily signal error {ticker}: {e}")
        return None


async def compute_1h_signals(ticker: str) -> Optional[dict]:
    df = await _get_data(ticker, days=10, interval="1H")
    if df is None or len(df) < 20:
        return None
    try:
        close = df["close"].astype(float)
        rsi = ta.momentum.RSIIndicator(close=close, window=14).rsi()
        macd_diff = ta.trend.MACD(close=close).macd_diff()
        macd_bearish = bool(macd_diff.iloc[-1] < 0 and macd_diff.iloc[-2] >= 0)
        return {
            "price": round(float(close.iloc[-1]), 2),
            "rsi_1h": round(float(rsi.iloc[-1]), 2),
            "macd_diff_1h": round(float(macd_diff.iloc[-1]), 4),
            "macd_bearish_crossover": macd_bearish,
        }
    except Exception as e:
        logger.error(f"1H signal error {ticker}: {e}")
        return None
