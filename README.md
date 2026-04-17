# Stock Bot VN

AI-powered Telegram bot for Vietnamese stock market analysis. Monitors news, runs sentiment analysis via Google Gemini 2.5 Flash, and combines technical indicators to deliver hourly Buy/Sell suggestions directly to your Telegram.

## Features

- Hourly market updates (weekdays 8:00–15:00 ICT)
- Daily end-of-day market recap at 16:00 ICT (weekdays) — indices, highlight events, sector leaders, buy/sell calls
- Two-layer AI sentiment scoring: ticker-specific (60%) + macro (40%)
- Technical signals: RSI, MACD, MA20/MA50, Volume breakout
- Triple-layer exit strategy: hard stop-loss, technical exit, trailing stop
- News deduplication — same story is never analyzed twice
- Multi-user public bot — anyone can subscribe and manage their own watchlist and positions
- Buy/sell suggestions with full reasoning in Vietnamese
- Disclaimer on every suggestion — not financial advice

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.9+ (async) |
| Bot | python-telegram-bot 21 |
| AI | Google Gemini 2.5 Flash (override via `GEMINI_MODEL` env var) |
| Market Data | vnstock |
| Technical Analysis | ta (RSI, MACD, SMA, Bollinger) |
| News Scraping | httpx + BeautifulSoup4 |
| Global Macro | yfinance + feedparser |
| Database | PostgreSQL (Supabase) |
| Deployment | Render |

## Prerequisites

You need three free accounts before starting:

| Service | Purpose | Where to get |
|---|---|---|
| Telegram Bot Token | Bot identity | `@BotFather` on Telegram |
| Google Gemini API Key | AI sentiment analysis | aistudio.google.com |
| Supabase Database URL | PostgreSQL database | supabase.com → Connect → Session Pooler |

## Setup

### 1. Clone and create virtual environment

```bash
git clone <repo-url>
cd stock-bot-vn
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` with your keys:

```env
TELEGRAM_BOT_TOKEN=   # from @BotFather on Telegram
GEMINI_API_KEY=       # from aistudio.google.com
DATABASE_URL=         # from Supabase → Connect → Session Pooler tab
```

> Note: If your database password contains special characters (#, @, etc.), wrap the entire value in double quotes in `.env`.

### 3. Run

```bash
# Start
.venv/bin/python main.py

# Stop
pkill -f "python main.py"
```

## Telegram Commands

| Command | Description |
|---|---|
| `/start` | Register, load VN30 watchlist, show full guide |
| `/help` | Show guide and disclaimer |
| `/subscribe` | Enable hourly market updates |
| `/unsubscribe` | Disable hourly updates |
| `/pause` | Temporarily pause updates |
| `/resume` | Resume updates |
| `/watchlist` | View your tracked tickers |
| `/add [TICKER]` | Add a ticker — e.g. `/add HPG` |
| `/remove [TICKER]` | Remove a ticker — e.g. `/remove HPG` |
| `/setportfolio [amount]` | Set portfolio value in VND — e.g. `/setportfolio 100000000` |
| `/buy [TICKER] [PRICE]` | Record a buy entry for exit monitoring — e.g. `/buy HPG 27000` |
| `/sell [TICKER]` | Close a position — e.g. `/sell HPG` |
| `/check [TICKER]` | Run full analysis on a ticker now — e.g. `/check HPG` |
| `/news` / `/news [TICKER]` | Latest macro news, or news for a specific ticker |

## Buy Signal Logic

A buy suggestion is only triggered when **all 5 conditions** pass:

1. **Trend** — Price > MA20 > MA50 (daily uptrend confirmed)
2. **Momentum** — RSI(14) between 45–65
3. **MACD** — Bullish crossover within the last 3 trading days
4. **Volume** — Today's volume > 20-day average
5. **Sentiment** — AI composite score > 0.6

## Sell Signal Logic (Triple-Layer)

Monitoring starts after you record a buy with `/buy`. Three independent triggers:

| Layer | Condition | Action |
|---|---|---|
| Hard Stop-Loss | Price drops -5% from entry | Immediate alert |
| Technical Exit | RSI(1H) > 75 or bearish MACD(1H) crossover | Profit-taking alert |
| Trailing Stop | Price reaches +3% from entry | Suggest moving stop to break-even |

## Daily Market Recap (4:00 PM ICT)

Every weekday at 16:00 ICT (~75 min after the ATC close at 14:45), the bot broadcasts an end-of-day report to every active subscriber. Broadcast-only (no on-demand command) to protect the Gemini quota.

Pipeline:

1. **Data gathering (parallel)** — VN-Index / VN30 / HNX-Index / UPCOM-Index OHLC via `vnstock`, top gainers/losers (with a VN30 fallback), global macro (DXY / S&P 500 / Gold / Oil), macro news (CafeF + RSS), and news for each top-mover ticker.
2. **Codified VN30 buy/sell scan** — a single pass over the VN30 computes daily technicals once, then derives:
   - **Buy candidates** — tickers passing the full 5-condition rule (trend, RSI 45–65, MACD crossover, volume, AI sentiment > 0.6). Gemini sentiment is only called for tech-passers to keep it fast.
   - **Sell flags** — tickers showing daily technical breakdown: price below MA20, RSI(14) > 70 (overbought), or MACD bearish crossover in the last 3 days. No Gemini cost.
3. **Single Gemini call** composes a concise Vietnamese Markdown report with four sections: *Sự kiện nổi bật*, *Diễn biến chỉ số*, *Ngành & cổ phiếu dẫn dắt*, *Khuyến nghị mua / bán*. Recommendations must quote the pre-computed buy/sell lists — Gemini cannot add tickers outside them.
4. **Telegram broadcast** — delivered to all active, non-paused subscribers; long reports are split on paragraph boundaries and fall back to plain text if the LLM produces malformed Markdown.

Sell flags are *market-level cautions* (technical breakdown), distinct from the per-position exit alerts that fire only after you record an entry via `/buy`.

The recap never fabricates signals — if either the buy or sell list is empty, the report explicitly says so.

## Project Structure

```
stock-bot-vn/
├── src/
│   ├── config.py           # Environment variables and trading constants
│   ├── handlers/
│   │   └── commands.py     # All Telegram command handlers
│   ├── models/
│   │   ├── database.py     # asyncpg connection pool
│   │   └── schema.py       # PostgreSQL table definitions
│   ├── scraper/
│   │   ├── cafef.py        # CafeF news scraper (ticker + macro)
│   │   ├── macro.py        # yfinance (DXY, S&P500, Gold) + RSS feeds
│   │   └── dedup.py        # News deduplication via SHA256 hash
│   ├── engine/
│   │   ├── technical.py    # RSI, MACD, MA signals via vnstock + ta
│   │   ├── sentiment.py    # Gemini AI two-layer sentiment scoring + daily recap prompt
│   │   ├── market_index.py # VN-Index / VN30 / HNX / UPCOM snapshot + top movers
│   │   └── decision.py     # Buy/sell signal logic and message formatting
│   └── scheduler/
│       └── jobs.py         # Hourly update loop, exit monitoring, 4 PM daily recap loop
├── .env                    # Secret keys (never committed)
├── .env.example            # Key template
├── requirements.txt
└── main.py                 # Entry point
```

## Default Configuration

| Parameter | Value |
|---|---|
| Portfolio allocation per ticker | 15% |
| Hard stop-loss | -5% from entry |
| Trailing stop activation | +3% from entry |
| RSI buy range | 45–65 |
| RSI overbought exit (1H) | > 75 |
| Sentiment buy threshold | > 0.6 |
| Default watchlist | VN30 tickers |
| Update schedule | Weekdays 8:00–15:00 ICT, hourly |
| Daily recap schedule | Weekdays 16:00 ICT, broadcast to all subscribers |
| Data failure alert | 2 consecutive failed cycles |

## Disclaimer

This bot provides informational analysis only based on technical indicators and AI. It is **NOT financial advice**. All investment decisions are solely your responsibility.
