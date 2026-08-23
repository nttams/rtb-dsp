"""`make migrate` on a clean database creates all four tables and the `us-east` cluster row."""

import pytest


@pytest.mark.asyncio
async def test_all_tables_exist(pool) -> None:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
            tables = {r[0] for r in await cur.fetchall()}
    for expected in ("clusters", "campaigns", "creatives", "budget_allocations"):
        assert expected in tables


@pytest.mark.asyncio
async def test_cluster_seeded(pool) -> None:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT name FROM clusters")
            names = {r[0] for r in await cur.fetchall()}
    assert "us-east" in names
