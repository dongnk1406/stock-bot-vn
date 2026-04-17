
# Project Plan: AI-Powered Personal Stock Trading Bot (Vietnam Market)

## 1. Executive Summary
The goal is to build a high-performance, cost-effective Telegram bot that monitors the Vietnamese stock market. It will aggregate news, utilize Generative AI (LLMs) for sentiment analysis, and combine technical indicators to provide actionable Buy/Sell suggestions delivered directly to your private Telegram chat.

## 2. Optimized Technical Stack
To maintain a "lean" profile for individual use, the following stack is recommended:
* **Language:** Python 3.10+ (Asynchronous)
* **AI Engine:** Google Gemini 2.5 Flash by default (overridable via `GEMINI_MODEL` env var). Gemini has a generous free tier for personal API usage.
* **Data Ingestion:**
    * *VN News:* `httpx` + `BeautifulSoup4` — scrapes CafeF, Vietstock, Tin Nhanh Chung Khoan (ticker news + "Vĩ mô" macro sections).
    * *Global Macro:* `yfinance` — tracks DXY (USD Index), S&P 500, Gold (XAU/USD).
    * *Key Events:* RSS feeds from Investing.com / DailyFX — monitors Fed rate decisions and US CPI releases.
    * *Market Data:* `Vnstock3` (The most robust library for VN market data).
* **Analytics:** `Pandas-TA` (For technical indicators: RSI, MACD, MA20, MA50, Bollinger Bands on daily candles for Buy signals and 1H candles for Sell/exit signals).
* **Database:** `SQLite` (Zero-configuration, file-based storage for news logs, Watchlist, and entry price tracking).
* **Deployment:** Dockerized container on a small VPS or a home server (Raspberry Pi/Always-on PC).

---

## 3. Core Functional Requirements

### A. Smart News Monitoring
* **Targeted Scraping:** Focus on key financial portals (CafeF, Vietstock, Tin Nhanh Chung Khoan) via HTML scraping using `httpx` + `BeautifulSoup4`.
* **Scraper Resilience:** HTML scraping is brittle — implement automatic retries (3x with backoff) and send a Telegram alert when a source returns unexpected HTML (site redesign detection).
* **Watchlist Filtering:** To avoid information overload, the bot only processes news related to specific tickers in your personal Watchlist (e.g., HPG, VCB, FPT).
* **Macro News Layer:** In addition to ticker-specific news, the bot also ingests macro news (US Fed decisions, USD/VND rate, global commodity prices, VN-Index trend) and evaluates how they indirectly impact each tracked sector/ticker.
* **Deduplication:** Use SQLite to hash news headlines, ensuring the AI doesn't analyze (and charge you for) the same story twice.

### B. AI Reasoning Logic
* **Two-Layer Sentiment Scoring:** Each analysis runs two passes:
    1. **Ticker-Specific Sentiment:** Score direct news about the ticker (-1 Bearish → 1 Bullish).
    2. **Macro Sentiment:** Score macro news (Fed rate, USD/VND, commodity prices, global indices) and translate its sector-level impact onto the ticker.
* **Final Composite Score:** Weighted average — Ticker Sentiment (60%) + Macro Sentiment (40%).
* **Contextual Analysis:** The AI evaluates how macro events specifically impact the ticker's sector (e.g., rising steel prices → Bullish for HPG; rising USD → Bearish for import-heavy sectors).
* **Summary:** Provide 3-bullet point summaries of long articles to save reading time.
* **Time Horizon Context:** The AI prompt is instructed to evaluate news impact over a 1–2 week horizon (swing trade perspective), ignoring short-term intraday noise.

### C. Technical-Fundamental Hybrid Signals
* **Time Frame:** Buy conditions use the **daily candle**; Sell/exit triggers monitor the **4H candle** for faster reaction.
* **Buy Condition Sync:** A "Buy Suggestion" is only triggered when **all 5 conditions** pass:
    1. **Trend:** Price > MA20 AND MA20 > MA50 (daily uptrend confirmed).
    2. **Momentum:** RSI(14) between 45–65 (not oversold entry, not overbought).
    3. **Crossover:** MACD bullish crossover occurred within the last 3 trading days.
    4. **Volume:** Today's volume > 20-day average volume (breakout confirmation, not a fake move).
    5. **Composite Sentiment:** AI composite score > 0.6 (slightly strict to reduce noise).

### D. Sell Signal — Triple-Layer Exit Strategy
Exit monitoring begins only after the user confirms an executed trade via `/buy [TICKER] [PRICE]`. The bot records the user's actual brokerage execution price for accurate risk calculations. One active position per ticker at a time.

* **Layer 1 — Hard Stop-Loss (Risk):** Alert immediately if price drops **-5% from entry price**. Suggest selling to protect capital.
* **Layer 2 — Technical Exit (Momentum, 1H candles):**
    * RSI(14) > 75 on the 1H candle → Overbought, suggest taking profit.
    * Bearish MACD crossover on the 1H candle → Momentum reversing, suggest exit.
    * Uses `vnstock3` native 1H intraday data — no resampling required.
* **Layer 3 — Trailing Stop (Profit Protection):** Once price hits **+3% from entry**, the bot suggests moving the stop-loss to break-even (entry price) to lock in a risk-free position.

* **Suggestion Only:** The bot provides suggestions — it has no permission to execute trades. All decisions are made by the user.
* **Alerting:** Push notifications via Telegram when a watchlist ticker meets all Buy conditions, any Sell layer is triggered, or a high-impact macro event is detected.

---

## 4. Optimization Strategies (Personal Use)

### I. Cost Optimization
* **Token Management:** Strip HTML tags and irrelevant metadata (ads, sidebars) from news articles before sending them to the AI to minimize token consumption.
* **Model Tiering (aspirational):** Use Gemini Flash tier for routine news sorting and only trigger more expensive models for complex portfolio rebalancing advice. Currently the bot uses a single model (default `gemini-2.5-flash`) for all AI calls.

### II. Performance Optimization
* **Asynchronous Execution:** Use `asyncio` to ensure the bot can scrape news, calculate indicators, and respond to user commands simultaneously without lag.
* **Caching:** Store technical analysis results for 5–15 minutes. If you ask for the same ticker analysis multiple times an hour, the bot retrieves it from memory instead of recalculating or re-fetching.

### III. Precision Optimization
* **Pre-Processing:** Do not ask the AI to calculate math. Calculate the RSI/MA in Python first, then pass those values into the AI prompt as "Facts."
* **Custom Prompting:** Use "System Instructions" to force the AI to act as a conservative Vietnamese fund manager, reducing "hallucinations" or overly aggressive advice.

---

## 4.5. Scheduled Update Behavior

### Hourly Market Update (Automated)
* **Schedule:** Every weekday (Monday–Friday), from **8:00 AM to 3:00 PM ICT (UTC+7)**, the bot sends one update per hour (8 updates/day total).
* **Timing Note:** HOSE trading hours are 9:00–11:30 AM and 1:00–2:45 PM. The 8–9 AM window covers pre-market news digestion; the 3 PM update covers end-of-session wrap-up.
* **Update Contents per Message:**
    1. **Macro Snapshot:** Key macro events since the last update (Fed news, USD/VND, commodity moves).
    2. **Watchlist Status:** For each ticker — current price, RSI, MACD status, and composite sentiment score.
    3. **Signal Alerts:** Flag any ticker that now meets all 5 Buy conditions (or previously met them and conditions have since broken).
    4. **Buy / Sell Suggestion (if triggered):** See reasoning format below.

### Daily Market Recap (End-of-Day, 4:00 PM ICT)
* **Schedule:** Every weekday (Monday–Friday) at **4:00 PM ICT** — runs inside the local bot via `daily_recap_loop` in `src/scheduler/jobs.py`.
* **Engine:** Gemini (`GEMINI_MODEL`, default `gemini-2.5-flash`) — a single LLM call composes the Markdown report. Shares the existing sentiment rate-limit lock to respect the free-tier 5 req/min quota.
* **Purpose:** End-of-day wrap-up delivered ~75 min after the ATC session closes at 2:45 PM, allowing time for official figures and post-close commentary to publish.
* **Data pulled before the LLM call:**
    * Index OHLC + change % + volume for VN-Index, VN30, HNX-Index, UPCOM-Index — via `vnstock.stock_historical_data(type="index")` (DNSE / EntradeX).
    * Top 5 gainers / losers — via `vnstock.market_top_mover` with a VN30 % change fallback if SSI is unavailable.
    * Global macro (DXY, S&P 500, Gold, Oil) — existing `fetch_global_macro` (yfinance).
    * Macro news (CafeF Vĩ mô + Investing/DailyFX RSS) — existing scrapers.
    * Ticker news for each top-mover ticker — existing CafeF scraper.
* **Report contents (Vietnamese Markdown):**
    1. **📌 Sự kiện nổi bật** — macro news, policy moves, corporate actions, foreign flows.
    2. **📈 Diễn biến chỉ số** — VN-Index / VN30 / HNX-Index / UPCOM-Index summary, breadth and liquidity commentary.
    3. **🎯 Ngành & cổ phiếu dẫn dắt** — leading / lagging sectors and the tickers driving them.
    4. **💡 Khuyến nghị mua / bán** — two grounded subsections:
        * **Buy** — only tickers passing the full 5-condition `check_buy_signal` rule (trend + RSI 45–65 + MACD bullish crossover ≤3 days + volume ≥ 20-day avg + AI sentiment > 0.6). Gemini must quote from this list or state "Không có mã qua đủ 5 điều kiện MUA hôm nay."
        * **Sell caution (market-level)** — VN30 tickers showing daily technical breakdown: price < MA20, RSI(14) > 70, or MACD bearish crossover within the last 3 days. These are *market-level* cautions distinct from per-position exits (which only fire after `/buy`). Gemini must quote from this list or state "Không có cảnh báo bán cấp thị trường hôm nay." Never fabricates signals.
* **Delivery:** Broadcast via Telegram to all active, non-paused subscribers. Messages longer than 3800 chars are split on paragraph boundaries. Falls back to plain text if the LLM produces malformed Markdown.
* **Broadcast-only:** No on-demand `/recap` command is exposed, to protect the Gemini free-tier quota (5 req/min) — each recap already consumes up to ~6 Gemini calls (sentiment on tech-passers + final composition), so allowing user spam would exhaust the quota.

### Suggestion Reasoning Format
All messages are in **Vietnamese**. Technical abbreviations (RSI, MACD, MA) remain in English.

Whenever the bot suggests a **Buy** or **Sell** with a recommended amount, it must explain its reasoning using this structure:

```
📌 [TICKER] — GỢI Ý MUA / BÁN
──────────────────────────────────
💰 Số lượng gợi ý: X cổ phiếu (~15% danh mục | ~X,XXX,XXX VNĐ)
⚠️ Rủi ro: X,XXX VNĐ (chênh lệch đến stop-loss -5%)

📊 Lý do Kỹ thuật:
   • Xu hướng: Giá (X,XXX) > MA20 (X,XXX) > MA50 (X,XXX) ✅
   • RSI(14): XX — vùng động lượng tốt ✅
   • MACD: Giao cắt tăng X ngày trước ✅
   • Khối lượng: Hôm nay Xm so với TB20 ngày Xm ✅

📰 Lý do Tin tức:
   • Điểm tin tức cổ phiếu: +0.X (vd: công bố hợp đồng mới)
   • Điểm vĩ mô: +0.X (vd: giá thép toàn cầu tăng)
   • Điểm tổng hợp: 0.X / 1.0 ✅

🛑 Thoát lệnh khi:
   • Stop-loss cứng: -5% từ giá vào lệnh
   • Chốt lời: RSI(4H) > 75 hoặc MACD(4H) cắt xuống
   • Trailing stop: Dời stop về hoà vốn khi lãi +3%
```

* **Ngôn ngữ:** Toàn bộ thông báo, tóm tắt tin tức, và gợi ý bằng **tiếng Việt**. Ký hiệu kỹ thuật (RSI, MACD, MA) giữ nguyên tiếng Anh.
* **Amount Logic:** Suggested share count = 15% of declared portfolio value per ticker. The user sets their portfolio size via `/setportfolio [amount]`.

---

## 5. Implementation Roadmap

### Phase 1: Data & Connectivity
* Configure Telegram Bot via `@BotFather`.
* Establish connection to Gemini API.
* Implement the `Vnstock3` interface to pull historical and real-time prices.

### Phase 2: The Scraper & Filter
* Build async scrapers for CafeF, Vietstock (ticker news + "Vĩ mô" sections).
* Integrate `yfinance` for DXY, S&P 500, Gold; add RSS parsing for Investing.com/DailyFX Fed/CPI events.
* Create an SQLite schema: `Watchlist`, `ProcessedNews`, `EntryPrices` (to track active positions for exit monitoring).
* Pre-load the **VN30** index tickers as the default watchlist at startup.
* Implement a keyword-matching engine to link news to specific tickers.

### Phase 3: The Intelligence Layer
* Develop the AI Prompt template (Vietnamese output, conservative fund manager persona).
* Integrate `Pandas-TA` to generate daily signals (Buy: MA, RSI, MACD, Volume) and 1H signals (Sell: RSI, MACD) using `vnstock3` native 1H data.
* Create the "Decision Engine" that merges News Sentiment + Technicals for Buy and Triple-Layer Exit for Sell.
* Implement the error heartbeat monitor: if any data source fails for 2 consecutive cycles (~30 min), push a Telegram alert: "⚠️ CẢNH BÁO: Mất kết nối dữ liệu (Scraper/API). Bot tạm ngừng cập nhật tín hiệu."

### Phase 4: Personal UI & Deployment
* Design Telegram commands: `/add [TICKER]`, `/remove [TICKER]`, `/news`, `/check [TICKER]`, `/watchlist`, `/setportfolio [amount]`, `/buy [TICKER] [PRICE]` (record entry for exit monitoring), `/sell [TICKER]` (close position tracking), and `/pause` / `/resume` (to temporarily stop hourly updates).
* Dockerize the application.
* Deploy to a 24/7 environment (VPS/Cloud).

---

## 6. Directory Structure (Clean Architecture)
```text
/stock-bot-vn
│
├── data/                   # SQLite database file
├── src/
│   ├── scraper/            # Fetch news (CafeF, Vietstock, RSS, yfinance)
│   ├── engine/             # AI analysis, technical indicators, decision engine
│   ├── handlers/           # Telegram command logic
│   ├── scheduler/          # Hourly job runner (8 AM–3 PM ICT, weekdays)
│   └── models/             # Database interactions (Watchlist, ProcessedNews, EntryPrices)
├── .env                    # API Keys & Bot Tokens
├── requirements.txt        # Dependencies
└── main.py                 # App entry point
```

## 7. Key Configuration Defaults
| Parameter | Default Value |
|---|---|
| Portfolio allocation per ticker | 15% |
| Stop-loss | -5% from entry |
| Trailing stop activation | +3% from entry |
| RSI overbought exit (1H) | > 75 |
| Composite sentiment buy threshold | > 0.6 |
| Default watchlist | VN30 tickers |
| Update schedule | Weekdays 8 AM–3 PM ICT, hourly |
| Daily recap schedule | Weekdays 4 PM ICT, local `daily_recap_loop` (Gemini, Telegram broadcast) |
| Data source failure alert | 2 consecutive failed cycles (~30 min) |
| Output language | Vietnamese (technical symbols in English) |