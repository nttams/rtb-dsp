"""Testcontainers fixtures: a throwaway Postgres + Redis per session (finding M21).

Every integration test gets isolated stores; `_clean_state` wipes campaign data and flushes
Redis before each test function so tests don't interfere with each other.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from rtbdsp_common.db import make_pool
from rtbdsp_common.migrate import apply_migrations
from rtbdsp_common.redis_client import ClusterRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "migrations"
LUA_DIR = REPO_ROOT / "lua"

CLUSTER_NAME = "us-east"


@pytest.fixture(scope="session")
def postgres_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def redis_container():
    with RedisContainer("redis:7-alpine") as r:
        yield r


@pytest.fixture(scope="session")
def database_url(postgres_container: PostgresContainer) -> str:
    raw = postgres_container.get_connection_url()
    # testcontainers returns a SQLAlchemy-style DSN (postgresql+psycopg2://...); psycopg
    # wants a plain postgresql:// URL.
    return raw.replace("postgresql+psycopg2://", "postgresql://")


@pytest.fixture(scope="session")
def redis_url(redis_container: RedisContainer) -> str:
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    return f"redis://{host}:{port}/0"


@pytest.fixture(scope="session", autouse=True)
def _migrated(database_url: str) -> None:
    apply_migrations(database_url, MIGRATIONS_DIR)


@pytest_asyncio.fixture
async def pool(database_url: str):
    p = make_pool(database_url)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


@pytest_asyncio.fixture(autouse=True)
async def _clean_state(pool, redis_url: str):
    """Runs before every test: campaigns table empty, Redis flushed, cluster row pointed at
    this session's testcontainer Redis (migration 0002 seeds it pointing at the compose
    service name, which does not resolve outside docker-compose).
    """
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM campaigns")
            await cur.execute(
                "INSERT INTO clusters (name, redis_url) VALUES (%s, %s) "
                "ON CONFLICT (name) DO UPDATE SET redis_url = EXCLUDED.redis_url",
                (CLUSTER_NAME, redis_url),
            )
        await conn.commit()

    r = aioredis.from_url(redis_url, decode_responses=True)
    await r.flushdb()
    await r.aclose()
    yield


@pytest_asyncio.fixture
async def cluster_id(pool) -> int:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT id FROM clusters WHERE name = %s", (CLUSTER_NAME,))
            row = await cur.fetchone()
            return row[0]


@pytest_asyncio.fixture
async def registry(pool):
    reg = ClusterRegistry(pool)
    await reg.refresh()
    try:
        yield reg
    finally:
        await reg.close()


@pytest.fixture
def redis_client(redis_url: str):
    return aioredis.from_url(redis_url, decode_responses=True)
