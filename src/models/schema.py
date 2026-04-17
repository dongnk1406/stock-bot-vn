from src.models.database import get_pool

_SCHEMA = """
CREATE TABLE IF NOT EXISTS subscribers (
    chat_id          BIGINT PRIMARY KEY,
    username         TEXT,
    first_name       TEXT,
    subscribed_at    TIMESTAMPTZ DEFAULT NOW(),
    is_active        BOOLEAN DEFAULT TRUE,
    is_paused        BOOLEAN DEFAULT FALSE,
    portfolio_value  BIGINT DEFAULT 0,
    update_interval  INT DEFAULT 30,
    last_updated_at  TIMESTAMPTZ DEFAULT NULL
);

ALTER TABLE subscribers ADD COLUMN IF NOT EXISTS update_interval INT DEFAULT 30;
ALTER TABLE subscribers ADD COLUMN IF NOT EXISTS last_updated_at TIMESTAMPTZ DEFAULT NULL;

CREATE TABLE IF NOT EXISTS watchlist (
    id        SERIAL PRIMARY KEY,
    chat_id   BIGINT REFERENCES subscribers(chat_id) ON DELETE CASCADE,
    ticker    VARCHAR(10) NOT NULL,
    added_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(chat_id, ticker)
);

CREATE TABLE IF NOT EXISTS processed_news (
    id              SERIAL PRIMARY KEY,
    hash            VARCHAR(64) UNIQUE NOT NULL,
    title           TEXT,
    source          VARCHAR(100),
    ticker          VARCHAR(10),
    sentiment_score FLOAT,
    processed_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS entry_prices (
    id           SERIAL PRIMARY KEY,
    chat_id      BIGINT REFERENCES subscribers(chat_id) ON DELETE CASCADE,
    ticker       VARCHAR(10) NOT NULL,
    entry_price  FLOAT NOT NULL,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    is_active    BOOLEAN DEFAULT TRUE,
    UNIQUE(chat_id, ticker)
);
"""


async def init_schema() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(_SCHEMA)
