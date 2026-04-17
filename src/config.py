import os
from urllib.parse import quote
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

VN30_TICKERS = [
    "ACB", "BCM", "BID", "BVH", "CTG", "FPT", "GAS", "GVR", "HDB", "HPG",
    "MBB", "MSN", "MWG", "PLX", "POW", "SAB", "SHB", "SSB", "SSI", "STB",
    "TCB", "TPB", "VCB", "VHM", "VIB", "VIC", "VJC", "VNM", "VPB", "VRE",
]

# Buy signal thresholds
RSI_MIN = 45
RSI_MAX = 65
RSI_OVERBOUGHT_1H = 75
MACD_LOOKBACK_DAYS = 3
VOLUME_MULTIPLIER = 1.0
SENTIMENT_BUY_THRESHOLD = 0.6

# Risk management
STOP_LOSS_PCT = 0.05
TRAILING_STOP_ACTIVATION_PCT = 0.03
PORTFOLIO_ALLOCATION_PCT = 0.15

# Scheduler
MARKET_OPEN_HOUR = 8
MARKET_CLOSE_HOUR = 15
MARKET_TZ = "Asia/Ho_Chi_Minh"


def get_database_url() -> str:
    """Return asyncpg-compatible DATABASE_URL with percent-encoded password."""
    url = os.getenv("DATABASE_URL", "")
    if not url.startswith("postgresql://"):
        return url

    rest = url[len("postgresql://"):]
    at_idx = rest.rfind("@")
    credentials = rest[:at_idx]
    host_part = rest[at_idx + 1:]
    colon_idx = credentials.index(":")
    user = credentials[:colon_idx]
    password = credentials[colon_idx + 1:]
    encoded_password = quote(password, safe="")
    return f"postgresql://{user}:{encoded_password}@{host_part}"
