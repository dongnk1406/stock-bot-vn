from __future__ import annotations
import asyncio
import re
import time
from collections import deque
from telegram import Update
from telegram.ext import ContextTypes
from src.models.database import get_pool
from src.config import VN30_TICKERS

_TICKER_RE = re.compile(r'^[A-Z0-9]{1,10}$')
_MAX_PORTFOLIO = 10 ** 15

# /check rate limit: max 3 calls per 5 min per chat_id (Gemini quota protection).
_CHECK_WINDOW_SEC = 300
_CHECK_MAX_CALLS = 3
_check_windows: dict[int, deque[float]] = {}


def _valid_ticker(ticker: str) -> bool:
    return bool(_TICKER_RE.match(ticker))


def _digits_only(s: str) -> str:
    """Strip everything except digits. Handles VN thousands separators ('.', ',', space)."""
    return "".join(c for c in s if c.isdigit())


def _check_rate_limit(chat_id: int) -> tuple[bool, int]:
    """Sliding-window limiter for /check. Returns (allowed, retry_after_seconds)."""
    now = time.monotonic()
    window = _check_windows.setdefault(chat_id, deque())
    while window and now - window[0] > _CHECK_WINDOW_SEC:
        window.popleft()
    if len(window) >= _CHECK_MAX_CALLS:
        return False, int(_CHECK_WINDOW_SEC - (now - window[0])) + 1
    window.append(now)
    return True, 0


_HELP_BODY = (
    "📖 HƯỚNG DẪN SỬ DỤNG\n"
    "─────────────────────────────\n\n"
    "Theo dõi thị trường:\n"
    "/subscribe — Bật nhận cập nhật mỗi 30 phút (T2-T6, 8:00-15:00)\n"
    "/unsubscribe — Tắt cập nhật\n"
    "/pause — Tạm dừng cập nhật\n"
    "/resume — Tiếp tục cập nhật\n\n"
    "Quản lý danh mục:\n"
    "/watchlist — Xem danh sách cổ phiếu đang theo dõi\n"
    "/add [TICKER] — Thêm cổ phiếu vào danh mục\n"
    "/remove [TICKER] — Xóa cổ phiếu khỏi danh mục\n"
    "/setportfolio [số tiền] — Cài giá trị danh mục (VND)\n"
    "/setinterval [phút] — Cài tần suất cập nhật (30/60/90/120 phút, mặc định 30)\n\n"
    "Quản lý vị thế:\n"
    "/buy [TICKER] [GIA] — Ghi nhận lệnh mua để theo dõi thoát lệnh\n"
    "/sell [TICKER] — Đóng vị thế, ngừng theo dõi thoát lệnh\n\n"
    "Phân tích:\n"
    "/check [TICKER] — Phân tích ngay một cổ phiếu (kỹ thuật + AI)\n"
    "/news — Xem tin tức vĩ mô mới nhất\n"
    "/news [TICKER] — Xem tin tức theo mã cổ phiếu\n"
    "\nHệ thống tự động gửi tổng kết phiên lúc 16:00 (T2–T6).\n"
)

_DISCLAIMER = (
    "─────────────────────────────\n"
    "⚠️ TUYÊN BỐ MIỄN TRỪ TRÁCH NHIỆM\n"
    "Bot này chỉ cung cấp thông tin tham khảo dựa trên phân tích kỹ thuật và AI. "
    "Đây KHÔNG phải lời khuyên đầu tư tài chính. "
    "Mọi quyết định mua/bán đều do bạn tự chịu trách nhiệm."
)


async def _ensure_subscriber(chat_id: int, username: str, first_name: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO subscribers (chat_id, username, first_name)
            VALUES ($1, $2, $3)
            ON CONFLICT (chat_id) DO UPDATE SET username = $2, first_name = $3
            """,
            chat_id, username, first_name,
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await _ensure_subscriber(user.id, user.username, user.first_name)

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE subscribers SET is_active = TRUE, is_paused = FALSE WHERE chat_id = $1",
            user.id,
        )
        count = await conn.fetchval("SELECT COUNT(*) FROM watchlist WHERE chat_id = $1", user.id)
        if count == 0:
            await conn.executemany(
                "INSERT INTO watchlist (chat_id, ticker) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                [(user.id, t) for t in VN30_TICKERS],
            )

    await update.message.reply_text(
        f"Xin chào {user.first_name}! 👋\n"
        "Chào mừng bạn đến với Stock Bot VN — bot phân tích chứng khoán Việt Nam.\n"
        "Danh mục theo dõi mặc định VN30 đã được tải.\n\n"
        + _HELP_BODY
        + "/help — Xem lại hướng dẫn này\n\n"
        + _DISCLAIMER
    )


async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await _ensure_subscriber(user.id, user.username, user.first_name)

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE subscribers SET is_active = TRUE, is_paused = FALSE WHERE chat_id = $1",
            user.id,
        )
    await update.message.reply_text("Đã bật nhận cập nhật mỗi 30 phút (T2–T6, 8:00–15:00).")


async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE subscribers SET is_active = FALSE WHERE chat_id = $1", user.id
        )
    await update.message.reply_text("Đã tắt cập nhật. Gõ /subscribe để bật lại.")


async def pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE subscribers SET is_paused = TRUE WHERE chat_id = $1", user.id
        )
    await update.message.reply_text("Đã tạm dừng cập nhật. Gõ /resume để tiếp tục.")


async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE subscribers SET is_paused = FALSE WHERE chat_id = $1", user.id
        )
    await update.message.reply_text("Đã tiếp tục nhận cập nhật hàng giờ.")


async def watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT ticker FROM watchlist WHERE chat_id = $1 ORDER BY ticker", user.id
        )

    if not rows:
        await update.message.reply_text("Danh mục theo dõi trống. Dùng /add [TICKER] để thêm.")
        return

    tickers = [r["ticker"] for r in rows]
    await update.message.reply_text(
        f"Danh mục theo dõi ({len(tickers)} cổ phiếu):\n" + "  ".join(tickers)
    )


async def add_ticker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("Cú pháp: /add [TICKER] — ví dụ: /add HPG")
        return

    ticker = context.args[0].upper()
    if not _valid_ticker(ticker):
        await update.message.reply_text("Mã cổ phiếu không hợp lệ.")
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchval(
            "SELECT COUNT(*) FROM subscribers WHERE chat_id = $1", user.id
        )
        if not existing:
            await _ensure_subscriber(user.id, user.username, user.first_name)

        result = await conn.execute(
            "INSERT INTO watchlist (chat_id, ticker) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            user.id, ticker,
        )

    if result == "INSERT 0 1":
        await update.message.reply_text(f"Đã thêm {ticker} vào danh mục theo dõi.")
    else:
        await update.message.reply_text(f"{ticker} đã có trong danh mục.")


async def remove_ticker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("Cú pháp: /remove [TICKER] — ví dụ: /remove HPG")
        return

    ticker = context.args[0].upper()
    if not _valid_ticker(ticker):
        await update.message.reply_text("Mã cổ phiếu không hợp lệ.")
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM watchlist WHERE chat_id = $1 AND ticker = $2", user.id, ticker
        )

    if result == "DELETE 1":
        await update.message.reply_text(f"Đã xóa {ticker} khỏi danh mục theo dõi.")
    else:
        await update.message.reply_text(f"{ticker} không có trong danh mục.")


async def set_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("Cú pháp: /setportfolio [số tiền] — ví dụ: /setportfolio 100000000")
        return

    digits = _digits_only(context.args[0])
    if not digits:
        await update.message.reply_text("Số tiền không hợp lệ. Nhập số nguyên VNĐ.")
        return
    amount = int(digits)

    if amount <= 0 or amount > _MAX_PORTFOLIO:
        await update.message.reply_text("Giá trị danh mục ngoài khoảng cho phép.")
        return

    await _ensure_subscriber(user.id, user.username, user.first_name)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE subscribers SET portfolio_value = $1 WHERE chat_id = $2", amount, user.id
        )
    await update.message.reply_text(
        f"Đã cập nhật giá trị danh mục: {amount:,.0f} VNĐ\n"
        f"Mỗi lệnh gợi ý tối đa 15% = {amount * 0.15:,.0f} VNĐ"
    )


async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if len(context.args) < 2:
        await update.message.reply_text("Cú pháp: /buy [TICKER] [GIÁ] — ví dụ: /buy HPG 25000")
        return

    ticker = context.args[0].upper()
    if not _valid_ticker(ticker):
        await update.message.reply_text("Mã cổ phiếu không hợp lệ.")
        return
    digits = _digits_only(context.args[1])
    if not digits:
        await update.message.reply_text("Giá không hợp lệ.")
        return
    price = float(digits)

    await _ensure_subscriber(user.id, user.username, user.first_name)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO entry_prices (chat_id, ticker, entry_price)
            VALUES ($1, $2, $3)
            ON CONFLICT (chat_id, ticker) DO UPDATE SET entry_price = $3, is_active = TRUE, created_at = NOW()
            """,
            user.id, ticker, price,
        )

    stop_loss = price * 0.95
    trailing = price * 1.03
    await update.message.reply_text(
        f"Đã ghi nhận lệnh mua {ticker} @ {price:,.0f} VNĐ\n\n"
        f"Stop-loss cứng: {stop_loss:,.0f} VNĐ (-5%)\n"
        f"Trailing stop kích hoạt tại: {trailing:,.0f} VNĐ (+3%)\n\n"
        "Bot sẽ theo dõi và cảnh báo nếu có tín hiệu thoát lệnh."
    )


async def sell(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("Cú pháp: /sell [TICKER] — ví dụ: /sell HPG")
        return

    ticker = context.args[0].upper()
    if not _valid_ticker(ticker):
        await update.message.reply_text("Mã cổ phiếu không hợp lệ.")
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE entry_prices SET is_active = FALSE WHERE chat_id = $1 AND ticker = $2 AND is_active = TRUE",
            user.id, ticker,
        )

    if result == "UPDATE 1":
        await update.message.reply_text(f"Đã đóng vị thế {ticker}. Bot ngừng theo dõi thoát lệnh cho cổ phiếu này.")
    else:
        await update.message.reply_text(f"Không tìm thấy vị thế đang mở cho {ticker}.")


VALID_INTERVALS = [30, 60, 90, 120]


async def set_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not context.args:
        await update.message.reply_text(
            "Cú pháp: /setinterval [phút]\n\n"
            f"Các mức hợp lệ: {', '.join(str(i) for i in VALID_INTERVALS)} phút\n"
            "Ví dụ: /setinterval 60\n\n"
            "Mặc định: 30 phút"
        )
        return

    try:
        minutes = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Giá trị không hợp lệ. Vui lòng nhập số phút.")
        return

    if minutes not in VALID_INTERVALS:
        await update.message.reply_text(
            f"Chỉ hỗ trợ các mức: {', '.join(str(i) for i in VALID_INTERVALS)} phút."
        )
        return

    await _ensure_subscriber(user.id, user.username, user.first_name)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE subscribers SET update_interval = $1 WHERE chat_id = $2",
            minutes, user.id,
        )

    await update.message.reply_text(
        f"Đã cập nhật tần suất cập nhật: mỗi {minutes} phút.\n"
        "Thay đổi có hiệu lực từ chu kỳ tiếp theo."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(_HELP_BODY + "\n" + _DISCLAIMER)


async def news(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ticker = context.args[0].upper() if context.args else None
    if ticker and not _valid_ticker(ticker):
        await update.message.reply_text("Mã cổ phiếu không hợp lệ.")
        return
    await update.message.reply_text(
        f"Đang lấy tin tức {'cho ' + ticker if ticker else 'vĩ mô'}..."
    )

    from src.scraper.cafef import fetch_ticker_news, fetch_macro_news
    from src.scraper.macro import fetch_rss_news

    if ticker:
        articles = await fetch_ticker_news(ticker, limit=5)
        header = f"📰 Tin tức mới nhất về {ticker}"
    else:
        cafef_articles, rss_articles = await asyncio.gather(
            fetch_macro_news(limit=5),
            fetch_rss_news(limit=5),
        )
        articles = cafef_articles + rss_articles
        header = "🌐 Tin tức vĩ mô mới nhất"

    if not articles:
        await update.message.reply_text("Không tìm thấy tin tức. Vui lòng thử lại sau.")
        return

    valid_articles = [a for a in articles if a.get("title", "").strip()]
    if not valid_articles:
        await update.message.reply_text("Không tìm thấy tin tức. Vui lòng thử lại sau.")
        return

    lines = [header, "─" * 30]
    for i, a in enumerate(valid_articles, 1):
        title = a.get("title", "").strip()
        url = a.get("url", "").strip()
        source = a.get("source", "").strip()
        summary = a.get("summary", "").strip()
        entry = f"{i}. *{title}*"
        if summary:
            entry += f"\n_{summary[:150]}{'...' if len(summary) > 150 else ''}_"
        entry += f"\n🔗 [{source}]({url})" if url else f"\n📌 {source}"
        lines.append(entry)

    await update.message.reply_text(
        "\n\n".join(lines),
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


async def check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user

    if not context.args:
        await update.message.reply_text(
            "Vui lòng cung cấp mã cổ phiếu — ví dụ: `/check HPG`\n\n"
            "_Phân tích toàn bộ danh mục sẽ được gửi tự động theo lịch cập nhật._",
            parse_mode="Markdown",
        )
        return

    ticker = context.args[0].upper()
    if not _valid_ticker(ticker):
        await update.message.reply_text("Mã cổ phiếu không hợp lệ.")
        return

    ok, retry_after = _check_rate_limit(user.id)
    if not ok:
        await update.message.reply_text(
            f"⏱️ Bạn đã dùng /check {_CHECK_MAX_CALLS} lần trong 5 phút vừa qua. "
            f"Vui lòng chờ {retry_after}s rồi thử lại."
        )
        return

    await update.message.reply_text(f"Đang phân tích {ticker}...")

    from src.scraper.cafef import fetch_ticker_news, fetch_macro_news
    from src.scraper.macro import fetch_global_macro, fetch_rss_news
    from src.engine.technical import compute_daily_signals, compute_1h_signals
    from src.engine.sentiment import analyze_sentiment
    from src.engine.decision import (
        check_buy_signal, check_sell_signals,
        format_buy_message, format_watchlist_status, format_conclusion,
    )

    pool = await get_pool()
    async with pool.acquire() as conn:
        portfolio_value = await conn.fetchval(
            "SELECT portfolio_value FROM subscribers WHERE chat_id = $1", user.id
        ) or 0

    # 1. Macro data (for sentiment context only, not displayed)
    macro_data, macro_news, rss_news = await asyncio.gather(
        fetch_global_macro(),
        fetch_macro_news(),
        fetch_rss_news(),
    )

    # 2. Technical + sentiment analysis
    technical = await compute_daily_signals(ticker)
    if technical is None:
        await update.message.reply_text(f"Không đủ dữ liệu cho {ticker}. Kiểm tra lại mã cổ phiếu.")
        return

    ticker_news = await fetch_ticker_news(ticker)
    all_macro_news = macro_news + rss_news
    sentiment = await analyze_sentiment(ticker, ticker_news, all_macro_news, macro_data)
    conditions = check_buy_signal(technical, sentiment)

    # 3. Watchlist status
    status = format_watchlist_status(ticker, technical, sentiment, conditions)
    await update.message.reply_text(
        f"📊 *DANH MỤC THEO DÕI:*\n\n{status}",
        parse_mode="Markdown",
    )

    # 4. Conclusion
    conclusion = format_conclusion([(ticker, technical, sentiment, conditions)], portfolio_value)
    await update.message.reply_text(conclusion, parse_mode="Markdown")

    # 5. Detailed buy message
    if conditions["signal"] and portfolio_value > 0:
        await update.message.reply_text(
            format_buy_message(ticker, technical, sentiment, conditions, portfolio_value)
        )

    # 6. Exit signals if there's an open position
    async with pool.acquire() as conn:
        entry = await conn.fetchval(
            "SELECT entry_price FROM entry_prices WHERE chat_id = $1 AND ticker = $2 AND is_active = TRUE",
            user.id, ticker,
        )
    if entry:
        signals_1h = await compute_1h_signals(ticker)
        if signals_1h:
            result = check_sell_signals(signals_1h, entry)
            for alert_type, message in result["alerts"]:
                emoji = "🚨" if alert_type == "stop_loss" else "⚠️"
                await update.message.reply_text(
                    f"{emoji} *{ticker}* — {message}\n"
                    f"Giá hiện tại: {result['price']:,.0f} VNĐ | Giá vào: {entry:,.0f} VNĐ\n\n"
                    f"⚠️ _Đây chỉ là gợi ý tham khảo, không phải lời khuyên tài chính._",
                    parse_mode="Markdown",
                )
