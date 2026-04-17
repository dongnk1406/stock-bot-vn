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
