import hashlib
from src.models.database import get_pool


def hash_title(title: str) -> str:
    return hashlib.sha256(title.strip().lower().encode()).hexdigest()


async def is_duplicate(title: str) -> bool:
    h = hash_title(title)
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM processed_news WHERE hash = $1)", h
        )


async def mark_processed(title: str, source: str, ticker: str, sentiment_score: float) -> None:
    h = hash_title(title)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO processed_news (hash, title, source, ticker, sentiment_score)
            VALUES ($1, $2, $3, $4, $5) ON CONFLICT DO NOTHING
            """,
            h, title, source, ticker, sentiment_score,
        )


async def prune_old_news(days: int = 30) -> int:
    """Delete processed_news rows older than `days`. Returns rows deleted."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            f"DELETE FROM processed_news WHERE processed_at < NOW() - INTERVAL '{int(days)} days'"
        )
    # asyncpg returns e.g. "DELETE 42"
    try:
        return int(result.split()[-1])
    except (ValueError, IndexError):
        return 0
