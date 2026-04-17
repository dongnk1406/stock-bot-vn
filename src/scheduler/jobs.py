from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone, timedelta
import pytz
from telegram import Bot
from telegram.error import NetworkError, TimedOut, RetryAfter
from src.config import (
    MARKET_TZ, VN30_TICKERS,
    RSI_MIN, RSI_MAX, MACD_LOOKBACK_DAYS, VOLUME_MULTIPLIER,
)
from src.models.database import get_pool
from src.scraper.cafef import fetch_ticker_news, fetch_macro_news
from src.scraper.macro import fetch_global_macro, fetch_rss_news
from src.scraper.dedup import is_duplicate, mark_processed, prune_old_news
from src.engine.technical import compute_daily_signals, compute_1h_signals
from src.engine.sentiment import analyze_sentiment, generate_daily_recap
from src.engine.decision import (
    check_buy_signal, check_sell_signals,
    format_buy_message, format_watchlist_status, format_conclusion,
)
from src.engine.market_index import fetch_index_snapshot, fetch_top_movers

logger = logging.getLogger(__name__)
_consecutive_failures = 0


async def _safe_send(bot: Bot, chat_id: int, text: str, parse_mode: str | None = None,
                     max_attempts: int = 3) -> bool:
    """Send a Telegram message with retry on transient network errors.

    Non-retryable errors (Forbidden, BadRequest) log and return False — usually a
    user has blocked the bot or the message payload is invalid; retrying won't help.
    """
    for attempt in range(max_attempts):
        try:
            kwargs = {"chat_id": chat_id, "text": text}
            if parse_mode:
                kwargs["parse_mode"] = parse_mode
            await bot.send_message(**kwargs)
            return True
        except RetryAfter as e:
            await asyncio.sleep(getattr(e, "retry_after", 5) + 1)
        except (NetworkError, TimedOut) as e:
            if attempt == max_attempts - 1:
                logger.error(f"Telegram send to {chat_id} failed after {max_attempts} attempts: {e}")
                return False
            logger.warning(f"Telegram send retry {attempt + 1}/{max_attempts} to {chat_id}: {e}")
            await asyncio.sleep(2 ** attempt)
        except Exception as e:
            logger.error(f"Telegram send to {chat_id} failed (non-retryable): {e}")
            return False
    return False

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

    await _safe_send(
        bot, chat_id,
        f"🕐 CẬP NHẬT THỊ TRƯỜNG — {now}\n"
        f"{'─'*34}\n"
        f"🌍 Vĩ mô toàn cầu:\n{macro_text}",
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
        await _safe_send(
            bot, chat_id,
            prefix + "\n\n".join(batch_lines) + suffix,
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
        await _safe_send(bot, chat_id, conclusion, parse_mode="Markdown")

    for ticker, technical, sentiment, conditions in all_results:
        if conditions["signal"] and portfolio_value > 0:
            await _safe_send(
                bot, chat_id,
                format_buy_message(ticker, technical, sentiment, conditions, portfolio_value),
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
                await _safe_send(
                    bot, chat_id,
                    f"{emoji} *{ticker}* — {message}\n"
                    f"Giá hiện tại: {result['price']:,.0f} VNĐ | Giá vào: {entry_price:,.0f} VNĐ\n\n"
                    f"⚠️ _Đây chỉ là gợi ý tham khảo, không phải lời khuyên tài chính. "
                    f"Mọi quyết định mua/bán đều do bạn tự chịu trách nhiệm._",
                    parse_mode="Markdown",
                )
        except Exception as e:
            logger.error(f"Exit signal check error {ticker}: {e}")


async def _broadcast_alert(bot: Bot, message: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        subscribers = await conn.fetch("SELECT chat_id FROM subscribers WHERE is_active = TRUE")
    for sub in subscribers:
        await _safe_send(bot, sub["chat_id"], message)


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


def _next_daily_recap_slot(now: datetime) -> datetime:
    """Return next Mon–Fri 16:00 ICT after `now`."""
    tz = pytz.timezone(MARKET_TZ)
    local = now.astimezone(tz)
    candidate = local.replace(hour=16, minute=0, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:  # skip Sat/Sun
        candidate += timedelta(days=1)
    return candidate


RSI_OVERBOUGHT_DAILY = 70
SELL_LOOKBACK_DAYS = 3


def _passes_buy_technicals(tech: dict) -> bool:
    """The 4 purely-technical conditions from check_buy_signal (no sentiment)."""
    return (
        tech["price"] > tech["ma20"] > tech["ma50"]
        and RSI_MIN <= tech["rsi"] <= RSI_MAX
        and tech.get("macd_crossover_days") is not None
        and tech["macd_crossover_days"] <= MACD_LOOKBACK_DAYS
        and tech["volume_ratio"] >= VOLUME_MULTIPLIER
    )


def _sell_reasons(tech: dict) -> list[str]:
    """Daily technical-breakdown flags. Any one triggers a caution."""
    reasons: list[str] = []
    if tech["price"] < tech["ma20"]:
        reasons.append("trend_break")
    if tech["rsi"] > RSI_OVERBOUGHT_DAILY:
        reasons.append("overbought")
    bearish = tech.get("macd_bearish_crossover_days")
    if bearish is not None and bearish <= SELL_LOOKBACK_DAYS:
        reasons.append("macd_bearish")
    return reasons


async def _scan_vn30(macro_data: dict, macro_news: list[dict]) -> tuple[list[dict], list[dict]]:
    """Single-pass VN30 scan: fetch daily technicals once, derive buy + sell lists.

    Buy side: the 5-condition check_buy_signal rule (technical gate → Gemini sentiment
    only for tech passers → composite threshold).

    Sell side: daily technical breakdown flags — price broke below MA20, RSI(14) > 70,
    or MACD bearish crossover within the last 3 days. No Gemini cost.
    """
    technicals = await asyncio.gather(
        *[compute_daily_signals(t) for t in VN30_TICKERS],
        return_exceptions=True,
    )

    tech_map: dict[str, dict] = {
        t: v for t, v in zip(VN30_TICKERS, technicals) if isinstance(v, dict)
    }

    # Sell flags: pure technicals, no Gemini.
    sell_flags: list[dict] = []
    for ticker, tech in tech_map.items():
        reasons = _sell_reasons(tech)
        if reasons:
            sell_flags.append({
                "ticker": ticker,
                "price": tech["price"],
                "ma20": tech["ma20"],
                "rsi": tech["rsi"],
                "macd_bearish_days": tech.get("macd_bearish_crossover_days"),
                "reasons": reasons,
            })

    # Buy side: filter by technicals first, then run Gemini sentiment only on passers.
    tech_passers = [(t, v) for t, v in tech_map.items() if _passes_buy_technicals(v)]
    buy_candidates: list[dict] = []
    if tech_passers:
        logger.info(f"Recap buy scan: {len(tech_passers)} tech passers — running sentiment.")
        for ticker, tech in tech_passers:
            try:
                ticker_news = await fetch_ticker_news(ticker, limit=3)
            except Exception:
                ticker_news = []
            sentiment = await analyze_sentiment(ticker, ticker_news, macro_news, macro_data)
            conditions = check_buy_signal(tech, sentiment)
            if conditions["signal"]:
                buy_candidates.append({
                    "ticker": ticker,
                    "price": tech["price"],
                    "ma20": tech["ma20"],
                    "ma50": tech["ma50"],
                    "rsi": tech["rsi"],
                    "macd_crossover_days": tech["macd_crossover_days"],
                    "volume_ratio": tech["volume_ratio"],
                    "composite_score": sentiment["composite_score"],
                    "ticker_reason": sentiment.get("ticker_reason", ""),
                })
    else:
        logger.info("Recap buy scan: 0 VN30 tickers passed technical gates.")

    logger.info(
        f"Recap scan result: {len(buy_candidates)} buy candidates, {len(sell_flags)} sell flags."
    )
    return buy_candidates, sell_flags


async def daily_market_recap(bot: Bot) -> str:
    """Build end-of-day market recap and broadcast to active subscribers."""
    tz = pytz.timezone(MARKET_TZ)
    trade_date = datetime.now(tz).strftime("%A %d/%m/%Y")

    logger.info("Daily recap: fetching market data...")
    indices, movers, macro_data, macro_news, rss_news = await asyncio.gather(
        fetch_index_snapshot(),
        fetch_top_movers(top_n=5),
        fetch_global_macro(),
        fetch_macro_news(limit=8),
        fetch_rss_news(limit=5),
    )

    # Ticker-level news: pool news for the top movers (union of gainers + losers).
    mover_tickers = list({r["ticker"] for r in (movers.get("gainers", []) + movers.get("losers", []))})[:6]
    news_results = await asyncio.gather(
        *[fetch_ticker_news(t, limit=2) for t in mover_tickers],
        return_exceptions=True,
    )
    ticker_news: list[dict] = []
    for t, result in zip(mover_tickers, news_results):
        if isinstance(result, list):
            ticker_news.extend(result)
        else:
            logger.warning(f"Recap ticker news fetch failed {t}: {result}")

    all_macro_news = (macro_news or []) + (rss_news or [])

    logger.info("Daily recap: running codified buy+sell scan over VN30...")
    buy_candidates, sell_flags = await _scan_vn30(macro_data, all_macro_news)

    logger.info("Daily recap: composing report via Gemini...")
    recap_md = await generate_daily_recap(
        indices=indices,
        movers=movers,
        macro_data=macro_data,
        macro_news=all_macro_news,
        ticker_news=ticker_news,
        trade_date=trade_date,
        buy_candidates=buy_candidates,
        sell_flags=sell_flags,
    )

    header = f"📊 *TỔNG KẾT PHIÊN — {trade_date}*\n{'─'*34}\n\n"
    body = header + recap_md

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT chat_id FROM subscribers WHERE is_active = TRUE AND is_paused = FALSE"
        )
    chat_ids = [r["chat_id"] for r in rows]

    for chat_id in chat_ids:
        try:
            await _send_chunked(bot, chat_id, body)
        except Exception as e:
            logger.error(f"Recap delivery failed for {chat_id}: {e}")

    logger.info(f"Daily recap delivered to {len(chat_ids)} chat(s).")

    try:
        pruned = await prune_old_news(days=30)
        if pruned:
            logger.info(f"Pruned {pruned} processed_news rows older than 30 days.")
    except Exception as e:
        logger.warning(f"processed_news prune failed: {e}")

    return recap_md


async def _send_chunked(bot: Bot, chat_id: int, text: str, limit: int = 3800) -> None:
    """Telegram caps messages at 4096 chars; split on paragraph boundaries.

    Uses _safe_send (retries transient errors) with a Markdown-then-plaintext
    fallback for LLM-produced content that may have malformed syntax.
    """
    async def _try_send(payload: str) -> None:
        if not await _safe_send(bot, chat_id, payload, parse_mode="Markdown"):
            await _safe_send(bot, chat_id, payload)

    if len(text) <= limit:
        await _try_send(text)
        return
    chunks: list[str] = []
    buf = ""
    for para in text.split("\n\n"):
        if len(buf) + len(para) + 2 > limit:
            if buf:
                chunks.append(buf)
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        chunks.append(buf)
    for chunk in chunks:
        await _try_send(chunk)


async def daily_recap_loop(bot: Bot) -> None:
    """Sleep until next Mon–Fri 16:00 ICT, emit recap, repeat."""
    while True:
        try:
            now_utc = datetime.now(timezone.utc)
            next_slot = _next_daily_recap_slot(now_utc)
            wait = (next_slot - now_utc).total_seconds()
            logger.info(f"Next daily recap at {next_slot:%Y-%m-%d %H:%M %Z} (in {wait/3600:.1f}h).")
            if wait > 0:
                await asyncio.sleep(wait)
            await daily_market_recap(bot)
        except asyncio.CancelledError:
            logger.info("Daily recap loop cancelled.")
            raise
        except Exception as e:
            logger.error(f"Daily recap loop error: {e}")
            # Back off briefly before re-computing the next slot.
            await asyncio.sleep(60)


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
