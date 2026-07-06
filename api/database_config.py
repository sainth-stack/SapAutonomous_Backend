"""
Async DB pool stub for SQLite mode.
The /api/configurations endpoints are not used by the three main pages,
so the pool is a no-op here.
"""

from __future__ import annotations
from contextlib import asynccontextmanager
from typing import AsyncGenerator


async def init_pool() -> None:
    pass


async def close_pool() -> None:
    pass


@asynccontextmanager
async def get_connection() -> AsyncGenerator:
    raise RuntimeError(
        "Async DB pool is not available in SQLite mode. "
        "This endpoint requires PostgreSQL configuration."
    )
    yield  # keeps it a valid async generator
