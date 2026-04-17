from __future__ import annotations
import asyncio
import json
import logging
import google.generativeai as genai
from src.config import GEMINI_API_KEY

logger = logging.getLogger(__name__)

genai.configure(api_key=GEMINI_API_KEY)
_model = genai.GenerativeModel("gemini-2.0-flash")

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
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _analyze_sync, ticker, ticker_news, macro_news, macro_data, sector)


def _analyze_sync(ticker, ticker_news, macro_news, macro_data, sector) -> dict:
    try:
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

        response = _model.generate_content(prompt)
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
        logger.error(f"Gemini error for {ticker}: {e}")
        return {
            "ticker_score": 0.0,
            "macro_score": 0.0,
            "composite_score": 0.5,
            "ticker_reason": "Lỗi phân tích AI",
            "macro_reason": "Lỗi phân tích AI",
            "summary": [],
        }
