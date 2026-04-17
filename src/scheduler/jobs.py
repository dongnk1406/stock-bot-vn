from __future__ import annotations
import logging
from datetime import datetime, timezone, timedelta
from telegram import Bot
from telegram.ext import CallbackContext
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


async def hourly_update(context: CallbackContext) -> None:
    global _consecutive_failures
    bot: Bot = context.bot
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

        for sub in subscribers:
            interval = sub["update_interval"] or 30
            last = sub["last_updated_at"]
            if last is not None and (now - last) < timedelta(minutes=interval):
                continue
            try:
                await _update_subscriber(bot, sub["chat_id"], sub["portfolio_value"] or 0, macro_data, all_macro_news)
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


async def _update_subscriber(bot: Bot, chat_id: int, portfolio_value: int, macro_data: dict, macro_news: list) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT ticker FROM watchlist WHERE chat_id = $1", chat_id)

    if not rows:
        return

    now = datetime.now().strftime("%H:%M %d/%m/%Y")

    # Build macro snapshot
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

    watchlist_lines = []
    all_results = []

    for row in rows:
        ticker = row["ticker"]
        try:
            raw_news = await fetch_ticker_news(ticker)
            new_news = [n for n in raw_news if not await is_duplicate(n["title"])]

            technical = await compute_daily_signals(ticker)
            if technical is None:
                watchlist_lines.append(f"*{ticker}* — ⚠️ Không đủ dữ liệu")
                continue

            sentiment = await analyze_sentiment(ticker, new_news, macro_news, macro_data)
            conditions = check_buy_signal(technical, sentiment)

            for n in new_news:
                await mark_processed(n["title"], n["source"], ticker, sentiment["composite_score"])

            watchlist_lines.append(format_watchlist_status(ticker, technical, sentiment, conditions))
            all_results.append((ticker, technical, sentiment, conditions))

        except Exception as e:
            logger.error(f"Ticker {ticker} analysis error: {e}")
            watchlist_lines.append(f"*{ticker}* — ⚠️ Lỗi phân tích")

    # Send watchlist status
    if watchlist_lines:
        await bot.send_message(
            chat_id=chat_id,
            text="📊 *DANH MỤC THEO DÕI:*\n\n" + "\n\n".join(watchlist_lines),
            parse_mode="Markdown",
        )

    # Send conclusion
    if all_results:
        conclusion = format_conclusion(all_results, portfolio_value)
        await bot.send_message(chat_id=chat_id, text=conclusion, parse_mode="Markdown")

    # Send detailed buy messages
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
