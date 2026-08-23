"""Seeding twice -> exactly 100 campaigns, zeroed Redis counters, empty resv_index (AC-28)."""

from __future__ import annotations

import pytest

from campaign_app.seeder import seed_demo
from rtbdsp_common import keys
from tests.helpers import CLUSTER_NAME, do_reserve, get_budget_field, get_stats_field
from topup_worker.main import run_topup_cycle


@pytest.mark.asyncio
async def test_seed_twice_yields_exactly_100_and_resets_redis_state(pool, registry, cluster_id) -> None:
    n1 = await seed_demo(pool, registry, cluster_id, CLUSTER_NAME, seed_min=20, seed_max=75, rng_seed=1)
    assert n1 == 100

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT count(*) FROM campaigns WHERE demo_batch = 'demo'")
            assert (await cur.fetchone())[0] == 100

    await run_topup_cycle(pool, registry)

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT id FROM campaigns WHERE demo_batch = 'demo' ORDER BY id LIMIT 1")
            first_id = (await cur.fetchone())[0]

    # simulate one bid against the first seeded campaign
    reserve_result = await do_reserve(registry, first_id, 1, "seed-test-resv")
    assert int(reserve_result[0]) == 1
    assert await get_stats_field(registry, first_id, "bids") == 1

    n2 = await seed_demo(pool, registry, cluster_id, CLUSTER_NAME, seed_min=20, seed_max=75, rng_seed=2)
    assert n2 == 100

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT count(*) FROM campaigns WHERE demo_batch = 'demo'")
            assert (await cur.fetchone())[0] == 100

    # the old campaign's Redis state must be gone, not carried into the reseeded batch
    assert await get_stats_field(registry, first_id, "bids") is None
    assert await get_budget_field(registry, first_id, "allocated") is None

    client = await registry.client_for_name(CLUSTER_NAME)
    assert await client.exists(keys.resv_index(CLUSTER_NAME)) == 0
    assert await client.exists(keys.reservation(CLUSTER_NAME, "seed-test-resv")) == 0


@pytest.mark.asyncio
async def test_seeded_campaigns_vary_meaningfully(pool, registry, cluster_id) -> None:
    await seed_demo(pool, registry, cluster_id, CLUSTER_NAME, seed_min=20, seed_max=75, rng_seed=7)

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT include_countries, include_languages, bid_cpm_micros, total_budget_micros "
                "FROM campaigns WHERE demo_batch = 'demo'"
            )
            rows = await cur.fetchall()

    assert len(rows) == 100
    untargeted = sum(1 for r in rows if not r[0] and not r[1])
    # ~20% untargeted -- assert it's meaningfully present without pinning an exact count
    assert 5 <= untargeted <= 40

    cpms = {r[2] for r in rows}
    budgets = {r[3] for r in rows}
    assert len(cpms) > 10  # meaningfully varied, not a handful of repeated values
    assert len(budgets) > 10
