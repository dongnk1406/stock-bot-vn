from src.config import (
    RSI_MIN, RSI_MAX, RSI_OVERBOUGHT_1H,
    MACD_LOOKBACK_DAYS, SENTIMENT_BUY_THRESHOLD,
    STOP_LOSS_PCT, TRAILING_STOP_ACTIVATION_PCT,
    PORTFOLIO_ALLOCATION_PCT,
)


def check_buy_signal(technical: dict, sentiment: dict) -> dict:
    price, ma20, ma50 = technical["price"], technical["ma20"], technical["ma50"]
    rsi = technical["rsi"]
    crossover_days = technical["macd_crossover_days"]
    volume_ratio = technical["volume_ratio"]
    composite = sentiment["composite_score"]

    cond = {
        "trend": price > ma20 and ma20 > ma50,
        "rsi": RSI_MIN <= rsi <= RSI_MAX,
        "macd_crossover": crossover_days is not None and crossover_days <= MACD_LOOKBACK_DAYS,
        "volume": volume_ratio >= 1.0,
        "sentiment": composite >= SENTIMENT_BUY_THRESHOLD,
    }
    return {"signal": all(cond.values()), "conditions": cond}


def check_sell_signals(technical_1h: dict, entry_price: float) -> dict:
    price = technical_1h["price"]
    rsi_1h = technical_1h["rsi_1h"]
    macd_bearish = technical_1h["macd_bearish_crossover"]
    pct = (price - entry_price) / entry_price

    alerts = []
    if pct <= -STOP_LOSS_PCT:
        alerts.append(("stop_loss", f"Giá giảm {pct*100:.1f}% — kích hoạt stop-loss cứng!"))
    if rsi_1h > RSI_OVERBOUGHT_1H:
        alerts.append(("rsi_exit", f"RSI(1H) = {rsi_1h:.0f} > 75 — vùng quá mua, cân nhắc chốt lời"))
    if macd_bearish:
        alerts.append(("macd_exit", "MACD(1H) giao cắt giảm — đà tăng suy yếu, cân nhắc thoát lệnh"))
    if pct >= TRAILING_STOP_ACTIVATION_PCT and not any(a[0] == "stop_loss" for a in alerts):
        alerts.append(("trailing", f"Đang lãi {pct*100:.1f}% — có thể dời stop về hoà vốn"))

    return {"alerts": alerts, "price": price, "pct_from_entry": pct}


def format_buy_message(ticker: str, technical: dict, sentiment: dict, conditions: dict, portfolio_value: int) -> str:
    price = technical["price"]
    alloc = portfolio_value * PORTFOLIO_ALLOCATION_PCT
    shares = int(alloc // price) if price > 0 else 0
    total_risk = price * STOP_LOSS_PCT * shares
    c = conditions["conditions"]
    tick = lambda b: "✅" if b else "❌"
    crossover = technical.get("macd_crossover_days", "N/A")
    vol_m = technical["volume_today"] / 1_000_000
    avg_m = technical["volume_avg20"] / 1_000_000
    bullets = "\n".join(f"   • {b}" for b in sentiment.get("summary", []))

    return (
        f"📌 {ticker} — GỢI Ý MUA\n"
        f"{'─'*34}\n"
        f"💰 Số lượng gợi ý: {shares:,} cổ (~15% | ~{alloc:,.0f} VNĐ)\n"
        f"⚠️ Rủi ro: {total_risk:,.0f} VNĐ (stop-loss -5%)\n\n"
        f"📊 Lý do Kỹ thuật:\n"
        f"   • Xu hướng: {price:,.0f} > MA20({technical['ma20']:,.0f}) > MA50({technical['ma50']:,.0f}) {tick(c['trend'])}\n"
        f"   • RSI(14): {technical['rsi']:.0f} {tick(c['rsi'])}\n"
        f"   • MACD: Giao cắt tăng {crossover} ngày trước {tick(c['macd_crossover'])}\n"
        f"   • Khối lượng: {vol_m:.1f}M vs TB20: {avg_m:.1f}M {tick(c['volume'])}\n\n"
        f"📰 Lý do Tin tức:\n"
        f"   • Tin cổ phiếu: {sentiment['ticker_score']:+.2f} — {sentiment['ticker_reason']}\n"
        f"   • Vĩ mô: {sentiment['macro_score']:+.2f} — {sentiment['macro_reason']}\n"
        f"   • Tổng hợp: {sentiment['composite_score']:.2f}/1.0 {tick(c['sentiment'])}\n\n"
        f"📝 Tóm tắt:\n{bullets}\n\n"
        f"🛑 Thoát lệnh khi:\n"
        f"   • Stop-loss cứng: -5% từ giá vào lệnh\n"
        f"   • Chốt lời: RSI(1H) > 75 hoặc MACD(1H) cắt xuống\n"
        f"   • Trailing stop: Dời stop về hoà vốn khi lãi +3%\n\n"
        f"{'─'*34}\n"
        f"⚠️ TUYÊN BỐ MIỄN TRỪ TRÁCH NHIỆM: Đây chỉ là gợi ý tham khảo dựa trên phân tích kỹ thuật và AI, "
        f"KHÔNG phải lời khuyên đầu tư tài chính. Mọi quyết định mua/bán đều do bạn tự chịu trách nhiệm. "
        f"Mọi quyết định mua/bán đều do bạn tự chịu trách nhiệm."
    )


def format_watchlist_status(ticker: str, technical: dict, sentiment: dict, conditions: dict) -> str:
    c = conditions["conditions"]
    tick = lambda b: "✅" if b else "❌"
    signal_label = "🟢 ĐỦ ĐIỀU KIỆN MUA" if conditions["signal"] else "⚪ Chưa đủ điều kiện"
    return (
        f"*{ticker}* — {technical['price']:,.0f} VNĐ | RSI: {technical['rsi']:.0f} | Score: {sentiment['composite_score']:.2f}\n"
        f"  Trend {tick(c['trend'])} RSI {tick(c['rsi'])} MACD {tick(c['macd_crossover'])} Vol {tick(c['volume'])} Tin {tick(c['sentiment'])}\n"
        f"  {signal_label}"
    )
