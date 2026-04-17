# Stock Bot VN

AI-powered Telegram bot for Vietnamese stock market analysis. Monitors news, runs sentiment analysis via Google Gemini, and combines technical indicators to deliver hourly Buy/Sell suggestions directly to your Telegram.

## Features

- Hourly market updates (weekdays 8:00–15:00 ICT)
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
| AI | Google Gemini 2.0 Flash |
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
│   │   ├── sentiment.py    # Gemini AI two-layer sentiment scoring
│   │   └── decision.py     # Buy/sell signal logic and message formatting
│   └── scheduler/
│       └── jobs.py         # Hourly update job + exit signal monitoring
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
| Data failure alert | 2 consecutive failed cycles |

## Disclaimer

This bot provides informational analysis only based on technical indicators and AI. It is **NOT financial advice**. All investment decisions are solely your responsibility.
