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
