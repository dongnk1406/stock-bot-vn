from __future__ import annotations
import asyncio
import json
import logging
import re
import time
import google.generativeai as genai
from src.config import GEMINI_API_KEY, GEMINI_MODEL

logger = logging.getLogger(__name__)

genai.configure(api_key=GEMINI_API_KEY)
_model = genai.GenerativeModel(GEMINI_MODEL)
logger.info(f"Gemini model: {GEMINI_MODEL}")

# Free tier: 5 requests/min => >=12s spacing. Use 13s for safety.
_MIN_INTERVAL_SECONDS = 13.0
_MAX_RETRIES = 2
_rate_lock = asyncio.Lock()
_last_call_ts = 0.0


async def _acquire_slot() -> None:
    global _last_call_ts
    async with _rate_lock:
        wait = _MIN_INTERVAL_SECONDS - (time.monotonic() - _last_call_ts)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call_ts = time.monotonic()


def _parse_retry_delay(err: Exception) -> float | None:
    m = re.search(r"retry[_ ]delay[^\d]*(\d+)", str(err), re.IGNORECASE)
    return float(m.group(1)) if m else None

_SYSTEM = (
    "Bạn là nhà quản lý quỹ người Việt Nam, thận trọng, chuyên phân tích TTCK Việt Nam. "
    "Đánh giá tin tức theo góc nhìn swing trade 1–2 tuần. "
    "Trả lời ĐÚNG định dạng JSON, không thêm văn bản nào khác."
)


async def analyze_sentiment(
    ticker: str,
    ticker_news: list[dict],
    macro_news: list[dict],
    macro_data: dict,
    sector: str = "",
) -> dict:
    ticker_text = "\n".join(f"- {n['title']}: {n.get('summary','')}" for n in ticker_news[:5]) or "Không có."
    macro_text = "\n".join(f"- {n['title']}" for n in macro_news[:5]) or "Không có."
    macro_summary = ", ".join(
        f"{k}: {v['price']} ({v['change_pct']:+.2f}%)" for k, v in macro_data.items()
    ) or "Không có dữ liệu."

    prompt = f"""{_SYSTEM}

Cổ phiếu: {ticker} (ngành: {sector or 'không xác định'})

TIN TỨC CỔ PHIẾU:
{ticker_text}

TIN VĨ MÔ:
{macro_text}

DỮ LIỆU VĨ MÔ:
{macro_summary}

Trả về JSON:
{{
  "ticker_score": <-1.0 đến 1.0>,
  "macro_score": <-1.0 đến 1.0>,
  "composite_score": <0.0 đến 1.0, = ticker_score*0.6*0.5 + 0.5 + macro_score*0.4*0.5 + 0.5*0.4>,
  "ticker_reason": "<lý do ngắn>",
  "macro_reason": "<lý do ngắn>",
  "summary": ["<bullet 1>", "<bullet 2>", "<bullet 3>"]
}}"""

    loop = asyncio.get_event_loop()
    for attempt in range(_MAX_RETRIES + 1):
        await _acquire_slot()
        try:
            response = await loop.run_in_executor(None, _model.generate_content, prompt)
            text = response.text.strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            data = json.loads(text.strip())
            return {
                "ticker_score": float(data.get("ticker_score", 0)),
                "macro_score": float(data.get("macro_score", 0)),
                "composite_score": min(max(float(data.get("composite_score", 0.5)), 0), 1),
                "ticker_reason": data.get("ticker_reason", ""),
                "macro_reason": data.get("macro_reason", ""),
                "summary": data.get("summary", []),
            }
        except Exception as e:
            is_429 = "429" in str(e) or "quota" in str(e).lower() or "exhaust" in str(e).lower()
            if is_429 and attempt < _MAX_RETRIES:
                delay = _parse_retry_delay(e) or (_MIN_INTERVAL_SECONDS * (attempt + 2))
                logger.warning(f"Gemini 429 for {ticker}, retrying in {delay:.0f}s (attempt {attempt+1}/{_MAX_RETRIES})")
                await asyncio.sleep(delay)
                continue
            logger.error(f"Gemini error for {ticker}: {e}")
            return {
                "ticker_score": 0.0,
                "macro_score": 0.0,
                "composite_score": 0.5,
                "ticker_reason": "Lỗi phân tích AI",
                "macro_reason": "Lỗi phân tích AI",
                "summary": [],
            }


_RECAP_SYSTEM = (
    "Bạn là nhà quản lý quỹ người Việt Nam, thận trọng, chuyên viết báo cáo tổng kết cuối phiên "
    "thị trường chứng khoán Việt Nam. Giọng văn chuyên nghiệp, súc tích, dùng bullet thay cho đoạn văn dài. "
    "Ưu tiên số liệu cụ thể. KHÔNG bịa số. Nếu dữ liệu thiếu, nói rõ là 'không có dữ liệu'."
)


def _fmt_indices(indices: dict) -> str:
    if not indices:
        return "Không có dữ liệu chỉ số."
    lines = []
    for name, d in indices.items():
        lines.append(
            f"- {name}: mở {d['open']}, cao {d['high']}, thấp {d['low']}, đóng {d['close']} "
            f"({d['change_abs']:+.2f} / {d['change_pct']:+.2f}%), KL {d['volume']:,.0f}"
        )
    return "\n".join(lines)


def _fmt_movers(movers: dict) -> str:
    def _fmt(rows: list[dict]) -> str:
        if not rows:
            return "n/a"
        return ", ".join(
            f"{r['ticker']} ({r['change_pct']:+.2f}%)" for r in rows
        )
    return f"Top tăng: {_fmt(movers.get('gainers', []))}\nTop giảm: {_fmt(movers.get('losers', []))}"


def _fmt_macro(macro_data: dict) -> str:
    if not macro_data:
        return "Không có dữ liệu vĩ mô."
    return ", ".join(
        f"{k} {v['price']} ({v['change_pct']:+.2f}%)" for k, v in macro_data.items()
    )


def _fmt_news(news: list[dict], limit: int = 8) -> str:
    if not news:
        return "Không có tin nổi bật."
    return "\n".join(f"- {n.get('title','').strip()}" for n in news[:limit] if n.get("title"))


def _fmt_buy_candidates(candidates: list[dict]) -> str:
    if not candidates:
        return (
            "KHÔNG có mã nào qua đủ 5 điều kiện MUA hôm nay. "
            "Phần khuyến nghị MUA BẮT BUỘC phải ghi rõ 'Không có mã qua đủ 5 điều kiện MUA hôm nay.'"
        )
    lines = []
    for c in candidates:
        lines.append(
            f"- {c['ticker']}: giá {c['price']:,.0f} > MA20 {c['ma20']:,.0f} > MA50 {c['ma50']:,.0f}; "
            f"RSI {c['rsi']:.0f}; MACD cắt lên {c['macd_crossover_days']} ngày trước; "
            f"khối lượng {c['volume_ratio']:.2f}× TB20; điểm AI {c['composite_score']:.2f}"
            + (f" — {c['ticker_reason']}" if c.get("ticker_reason") else "")
        )
    return "\n".join(lines)


_SELL_REASON_LABEL = {
    "trend_break": "giá thủng MA20",
    "overbought": "RSI(14) > 70 — quá mua",
    "macd_bearish": "MACD cắt xuống",
}


def _fmt_sell_flags(flags: list[dict]) -> str:
    if not flags:
        return (
            "KHÔNG có mã nào trong VN30 cho tín hiệu kỹ thuật suy yếu hôm nay. "
            "Phần cảnh báo BÁN BẮT BUỘC phải ghi rõ 'Không có cảnh báo bán cấp thị trường hôm nay.'"
        )
    lines = []
    for f in flags:
        reasons = ", ".join(_SELL_REASON_LABEL.get(r, r) for r in f["reasons"])
        bearish = f.get("macd_bearish_days")
        extra = f" (MACD cắt xuống {bearish} ngày trước)" if "macd_bearish" in f["reasons"] and bearish else ""
        lines.append(
            f"- {f['ticker']}: giá {f['price']:,.0f} vs MA20 {f['ma20']:,.0f}; "
            f"RSI {f['rsi']:.0f}; tín hiệu: {reasons}{extra}"
        )
    return "\n".join(lines)


async def generate_daily_recap(
    indices: dict,
    movers: dict,
    macro_data: dict,
    macro_news: list[dict],
    ticker_news: list[dict],
    trade_date: str,
    buy_candidates: list[dict] | None = None,
    sell_flags: list[dict] | None = None,
) -> str:
    """Compose an end-of-day Vietnamese Markdown recap via a single Gemini call."""
    prompt = f"""{_RECAP_SYSTEM}

Ngày giao dịch: {trade_date}

CHỈ SỐ THỊ TRƯỜNG:
{_fmt_indices(indices)}

TOP DIỄN BIẾN:
{_fmt_movers(movers)}

VĨ MÔ TOÀN CẦU:
{_fmt_macro(macro_data)}

TIN VĨ MÔ NỔI BẬT:
{_fmt_news(macro_news)}

TIN CỔ PHIẾU NỔI BẬT:
{_fmt_news(ticker_news)}

MÃ QUA ĐỦ 5 ĐIỀU KIỆN MUA (đã được hệ thống lọc cơ học, KHÔNG phải do bạn suy diễn):
{_fmt_buy_candidates(buy_candidates or [])}

MÃ CÓ TÍN HIỆU KỸ THUẬT SUY YẾU (daily — đã được hệ thống lọc cơ học):
{_fmt_sell_flags(sell_flags or [])}

Hãy viết báo cáo Markdown tiếng Việt với đúng 4 phần sau, giữ ngắn gọn (tổng cộng <3500 ký tự):

## 📌 Sự kiện nổi bật
- Bullet các sự kiện vĩ mô, chính sách, doanh nghiệp, dòng vốn ngoại đáng chú ý hôm nay.

## 📈 Diễn biến chỉ số
- Tóm tắt biến động VN-Index / VN30 / HNX-Index / UPCOM-Index (chỉ liệt kê chỉ số có dữ liệu).
- Nhận định ngắn về độ rộng thị trường và thanh khoản.

## 🎯 Ngành & cổ phiếu dẫn dắt
- Bullet ngành dẫn dắt / suy yếu, nêu tên các mã tiêu biểu (dựa vào top diễn biến).

## 💡 Khuyến nghị mua / bán
**Khuyến nghị MUA:**
- QUY TẮC BẮT BUỘC: chỉ được khuyến nghị MUA những mã nằm trong danh sách "MÃ QUA ĐỦ 5 ĐIỀU KIỆN MUA". KHÔNG được đề xuất bất kỳ mã nào nằm ngoài danh sách đó, kể cả khi mã đó xuất hiện trong top tăng giá.
- Với mỗi mã khuyến nghị, nêu trigger dựa trên các chỉ số đã cho (ví dụ: "MACD cắt lên 2 ngày, RSI 58, điểm AI 0.72").
- Nếu danh sách trống, viết chính xác: "Không có mã qua đủ 5 điều kiện MUA hôm nay."

**Cảnh báo BÁN / kỹ thuật suy yếu:**
- QUY TẮC BẮT BUỘC: chỉ được cảnh báo BÁN cho các mã nằm trong danh sách "MÃ CÓ TÍN HIỆU KỸ THUẬT SUY YẾU". KHÔNG suy diễn, KHÔNG thêm mã ngoài danh sách.
- Đây là cảnh báo CẤP THỊ TRƯỜNG (dựa trên kỹ thuật daily), KHÔNG phải lệnh thoát cho vị thế cá nhân. Cảnh báo thoát lệnh theo giá vào cụ thể được gửi riêng khi user đã ghi nhận /buy.
- Với mỗi mã, nêu lý do cụ thể (ví dụ: "RSI 78 — quá mua", "giá thủng MA20", "MACD cắt xuống 1 ngày").
- Nếu danh sách trống, viết chính xác: "Không có cảnh báo bán cấp thị trường hôm nay."

Kết thúc bằng một dòng disclaimer: "_Đây chỉ là thông tin tham khảo, không phải lời khuyên đầu tư._"

Xuất trực tiếp Markdown, KHÔNG bọc trong ```."""

    loop = asyncio.get_event_loop()
    for attempt in range(_MAX_RETRIES + 1):
        await _acquire_slot()
        try:
            response = await loop.run_in_executor(None, _model.generate_content, prompt)
            text = (response.text or "").strip()
            if text.startswith("```"):
                text = text.strip("`")
                if text.startswith("markdown"):
                    text = text[len("markdown"):]
            return text.strip()
        except Exception as e:
            is_429 = "429" in str(e) or "quota" in str(e).lower() or "exhaust" in str(e).lower()
            if is_429 and attempt < _MAX_RETRIES:
                delay = _parse_retry_delay(e) or (_MIN_INTERVAL_SECONDS * (attempt + 2))
                logger.warning(f"Gemini 429 on recap, retrying in {delay:.0f}s")
                await asyncio.sleep(delay)
                continue
            logger.error(f"Recap Gemini error: {e}")
            return (
                "⚠️ *Không thể tạo bản tổng kết do lỗi AI.*\n\n"
                "Vui lòng thử lại sau.\n\n"
                "_Đây chỉ là thông tin tham khảo, không phải lời khuyên đầu tư._"
            )
