from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone, timedelta
import pytz
from telegram import Bot
from src.config import MARKET_TZ
from src.models.database import get_pool
from src.scraper.cafef import fetch_ticker_news, fetch_macro_news
from src.scraper.macro import fetch_global_macro, fetch_rss_news
from src.scraper.dedup import is_duplicate, mark_processed
from src.engine.technical import compute_daily_signals, compute_1h_signals
from src.engine.sentiment import analyze_sentiment
from src.engine.decision import (
    check_buy_signal, check_sell_signals,
    format_buy_message, format_watchlist_status, format_conclusion,
)

logger = logging.getLogger(__name__)
_consecutive_failures = 0

# Per-cycle pacing. Gemini free tier = 5 req/min => 13s spacing (matches sentiment.py).
SECONDS_PER_TICKER = 13
CYCLE_BUFFER_SECONDS = 60
BATCH_SIZE = 5


async def _get_unique_watchlist_tickers() -> list[str]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT w.ticker FROM watchlist w "
            "JOIN subscribers s ON w.chat_id = s.chat_id "
            "WHERE s.is_active = TRUE AND s.is_paused = FALSE"
        )
    return [r["ticker"] for r in rows]


def estimate_cycle_duration_seconds(unique_ticker_count: int) -> int:
    return unique_ticker_count * SECONDS_PER_TICKER + CYCLE_BUFFER_SECONDS


async def _analyze_ticker_once(ticker: str, macro_data: dict, macro_news: list) -> dict | None:
    try:
        raw_news = await fetch_ticker_news(ticker)
        new_news = [n for n in raw_news if not await is_duplicate(n["title"])]

        technical = await compute_daily_signals(ticker)
        if technical is None:
            return {"technical": None, "sentiment": None, "conditions": None, "new_news": []}

        sentiment = await analyze_sentiment(ticker, new_news, macro_news, macro_data)
        conditions = check_buy_signal(technical, sentiment)

        for n in new_news:
            await mark_processed(n["title"], n["source"], ticker, sentiment["composite_score"])

        return {
            "technical": technical,
            "sentiment": sentiment,
            "conditions": conditions,
            "new_news": new_news,
        }
    except Exception as e:
        logger.error(f"Ticker {ticker} analysis error: {e}")
        return None


async def hourly_update(bot: Bot) -> None:
    """Run one full analysis cycle: fetch macro, analyze each unique ticker once, fan out per subscriber."""
    global _consecutive_failures
    try:
        macro_data = await fetch_global_macro()
        macro_news = await fetch_macro_news()
        rss_news = await fetch_rss_news()
        all_macro_news = macro_news + rss_news

        pool = await get_pool()
        now = datetime.now(timezone.utc)
        async with pool.acquire() as conn:
            subscribers = await conn.fetch(
                "SELECT chat_id, portfolio_value, update_interval, last_updated_at "
                "FROM subscribers WHERE is_active = TRUE AND is_paused = FALSE"
            )

        due_subscribers = []
        for sub in subscribers:
            interval = sub["update_interval"] or 30
            last = sub["last_updated_at"]
            if last is None or (now - last) >= timedelta(minutes=interval):
                due_subscribers.append(sub)

        if not due_subscribers:
            logger.info("No subscribers due for update; skipping cycle.")
            _consecutive_failures = 0
            return

        # Union of tickers across due subscribers' watchlists
        async with pool.acquire() as conn:
            chat_ids = [s["chat_id"] for s in due_subscribers]
            wl_rows = await conn.fetch(
                "SELECT DISTINCT ticker FROM watchlist WHERE chat_id = ANY($1::bigint[])",
                chat_ids,
            )
        unique_tickers = sorted(r["ticker"] for r in wl_rows)
        logger.info(f"Analyzing {len(unique_tickers)} unique tickers for {len(due_subscribers)} subscribers.")

        # Analyze each unique ticker once
        cache: dict[str, dict | None] = {}
        for t in unique_tickers:
            cache[t] = await _analyze_ticker_once(t, macro_data, all_macro_news)

        # Fan out per subscriber
        for sub in due_subscribers:
            try:
                await _deliver_to_subscriber(
                    bot, sub["chat_id"], sub["portfolio_value"] or 0,
                    macro_data, cache,
                )
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE subscribers SET last_updated_at = $1 WHERE chat_id = $2",
                        now, sub["chat_id"],
                    )
            except Exception as e:
                logger.error(f"Update failed for {sub['chat_id']}: {e}")

        _consecutive_failures = 0

    except Exception as e:
        _consecutive_failures += 1
        logger.error(f"Update job failed ({_consecutive_failures}/2): {e}")
        if _consecutive_failures >= 2:
            await _broadcast_alert(bot, "⚠️ CẢNH BÁO: Mất kết nối dữ liệu. Bot tạm ngừng cập nhật tín hiệu.")
            _consecutive_failures = 0


async def _deliver_to_subscriber(
    bot: Bot,
    chat_id: int,
    portfolio_value: int,
    macro_data: dict,
    cache: dict[str, dict | None],
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT ticker FROM watchlist WHERE chat_id = $1", chat_id)

    if not rows:
        return

    now = datetime.now().strftime("%H:%M %d/%m/%Y")

    if macro_data:
        macro_lines = []
        for name, data in macro_data.items():
            arrow = "📈" if data["change_pct"] > 0 else "📉"
            macro_lines.append(f"  {arrow} {name}: {data['price']:,.2f} ({data['change_pct']:+.2f}%)")
        macro_text = "\n".join(macro_lines)
    else:
        macro_text = "  ⚠️ Không lấy được dữ liệu vĩ mô (Yahoo Finance tạm thời không phản hồi)"

    await bot.send_message(
        chat_id=chat_id,
        text=(
            f"🕐 CẬP NHẬT THỊ TRƯỜNG — {now}\n"
            f"{'─'*34}\n"
            f"🌍 Vĩ mô toàn cầu:\n{macro_text}"
        ),
    )

    all_results: list[tuple] = []
    batch_lines: list[str] = []
    total = len(rows)
    header_sent = False

    async def _flush_batch(done: int) -> None:
        nonlocal batch_lines, header_sent
        if not batch_lines:
            return
        prefix = "📊 *DANH MỤC THEO DÕI:*\n\n" if not header_sent else ""
        suffix = f"\n\n_Đã gửi {done}/{total}..._" if done < total else ""
        await bot.send_message(
            chat_id=chat_id,
            text=prefix + "\n\n".join(batch_lines) + suffix,
            parse_mode="Markdown",
        )
        header_sent = True
        batch_lines = []

    for idx, row in enumerate(rows, start=1):
        ticker = row["ticker"]
        result = cache.get(ticker)
        if result is None:
            batch_lines.append(f"*{ticker}* — ⚠️ Lỗi phân tích")
        elif result["technical"] is None:
            batch_lines.append(f"*{ticker}* — ⚠️ Không đủ dữ liệu")
        else:
            technical = result["technical"]
            sentiment = result["sentiment"]
            conditions = result["conditions"]
            batch_lines.append(format_watchlist_status(ticker, technical, sentiment, conditions))
            all_results.append((ticker, technical, sentiment, conditions))

        if idx % BATCH_SIZE == 0 or idx == total:
            await _flush_batch(idx)

    if all_results:
        conclusion = format_conclusion(all_results, portfolio_value)
        await bot.send_message(chat_id=chat_id, text=conclusion, parse_mode="Markdown")

    for ticker, technical, sentiment, conditions in all_results:
        if conditions["signal"] and portfolio_value > 0:
            await bot.send_message(
                chat_id=chat_id,
                text=format_buy_message(ticker, technical, sentiment, conditions, portfolio_value),
            )

    await _check_exit_signals(bot, chat_id)


async def _check_exit_signals(bot: Bot, chat_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        positions = await conn.fetch(
            "SELECT ticker, entry_price FROM entry_prices WHERE chat_id = $1 AND is_active = TRUE", chat_id
        )

    for pos in positions:
        ticker, entry_price = pos["ticker"], pos["entry_price"]
        try:
            signals = await compute_1h_signals(ticker)
            if not signals:
                continue
            result = check_sell_signals(signals, entry_price)
            for alert_type, message in result["alerts"]:
                emoji = "🚨" if alert_type == "stop_loss" else "⚠️"
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"{emoji} *{ticker}* — {message}\n"
                        f"Giá hiện tại: {result['price']:,.0f} VNĐ | Giá vào: {entry_price:,.0f} VNĐ\n\n"
                        f"⚠️ _Đây chỉ là gợi ý tham khảo, không phải lời khuyên tài chính. "
                        f"Mọi quyết định mua/bán đều do bạn tự chịu trách nhiệm._"
                    ),
                    parse_mode="Markdown",
                )
        except Exception as e:
            logger.error(f"Exit signal check error {ticker}: {e}")


async def _broadcast_alert(bot: Bot, message: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        subscribers = await conn.fetch("SELECT chat_id FROM subscribers WHERE is_active = TRUE")
    for sub in subscribers:
        try:
            await bot.send_message(chat_id=sub["chat_id"], text=message)
        except Exception:
            pass


def _next_delivery_slot(now: datetime) -> datetime:
    """Return next Mon–Fri slot at :00 or :30 within 08:00–15:30 ICT."""
    tz = pytz.timezone(MARKET_TZ)
    local = now.astimezone(tz)
    candidate = local.replace(second=0, microsecond=0)
    if candidate.minute < 30:
        candidate = candidate.replace(minute=30)
    else:
        candidate = (candidate + timedelta(hours=1)).replace(minute=0)
    while True:
        if candidate.weekday() >= 5:  # Sat/Sun
            candidate = (candidate + timedelta(days=1)).replace(hour=8, minute=0)
            continue
        if candidate.hour < 8:
            candidate = candidate.replace(hour=8, minute=0)
        if candidate.hour > 15 or (candidate.hour == 15 and candidate.minute > 30):
            candidate = (candidate + timedelta(days=1)).replace(hour=8, minute=0)
            continue
        if candidate > local:
            return candidate
        candidate += timedelta(minutes=30)


async def analysis_loop(bot: Bot) -> None:
    """Continuous scheduler: times each cycle to finish at the next :00/:30 delivery slot."""
    tz = pytz.timezone(MARKET_TZ)
    while True:
        try:
            unique_tickers = await _get_unique_watchlist_tickers()
            duration_sec = estimate_cycle_duration_seconds(len(unique_tickers))
            now_utc = datetime.now(timezone.utc)
            next_slot = _next_delivery_slot(now_utc)
            start_at = next_slot - timedelta(seconds=duration_sec)
            now_local = now_utc.astimezone(tz)
            wait = (start_at - now_local).total_seconds()
            if wait > 0:
                logger.info(
                    f"Next cycle: {len(unique_tickers)} tickers, est {duration_sec}s. "
                    f"Waking at {start_at:%H:%M:%S} to deliver by {next_slot:%H:%M} ICT."
                )
                await asyncio.sleep(wait)
            else:
                logger.info(
                    f"Starting cycle immediately: est duration {duration_sec}s "
                    f"exceeds time to next slot {next_slot:%H:%M} ICT."
                )
            await hourly_update(bot)
        except asyncio.CancelledError:
            logger.info("Analysis loop cancelled.")
            raise
        except Exception as e:
            logger.error(f"Analysis loop error: {e}")
            await asyncio.sleep(60)
