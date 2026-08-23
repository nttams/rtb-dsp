"""Topup worker regressions: M8 (allocation floor + clamp flag), M9 (orphan reap),
and paused campaigns not topped up.
"""

from __future__ import annotations

import pytest

from tests.helpers import (
    CLUSTER_NAME,
    create_allocation,
    create_campaign,
    do_reserve,
    do_settle,
    do_topup,
    get_budget_field,
)
from topup_worker.main import run_topup_cycle


@pytest.mark.asyncio
async def test_m8_allocation_clamps_at_reserved_plus_spent(registry, cluster_id) -> None:
    campaign_id = 4242  # any int works -- topup.lua doesn't touch Postgres

    # seed the hash with a $10 allocation
    result = await do_topup(registry, campaign_id, allocated_micros=10_000_000, pg_spent_micros=0)
    assert int(result[3]) == 0  # not clamped yet

    # $6 spent
    await do_reserve(registry, campaign_id, 6_000_000, "r-spent")
    await do_settle(registry, campaign_id, "r-spent", 6_000_000)
    # $1 reserved
    await do_reserve(registry, campaign_id, 1_000_000, "r-reserved")

    assert await get_budget_field(registry, campaign_id, "spent") == 6_000_000
    assert await get_budget_field(registry, campaign_id, "reserved") == 1_000_000

    # lower the Postgres allocation to $5 -- below reserved+spent ($7)
    result = await do_topup(registry, campaign_id, allocated_micros=5_000_000, pg_spent_micros=6_000_000)
    allocated, reserved, spent, clamped = (int(x) for x in result)

    assert clamped == 1
    assert allocated == 7_000_000  # floored at reserved + spent
    assert reserved == 1_000_000
    assert spent == 6_000_000
    assert await get_budget_field(registry, campaign_id, "allocation_clamped") == 1


@pytest.mark.asyncio
async def test_m9_orphan_reap(pool, registry, cluster_id) -> None:
    keep_id = await create_campaign(pool, name="keeper")
    gone_id = await create_campaign(pool, name="doomed")
    await create_allocation(pool, keep_id, cluster_id, 1_000_000)
    await create_allocation(pool, gone_id, cluster_id, 1_000_000)

    await run_topup_cycle(pool, registry)
    assert await get_budget_field(registry, keep_id, "allocated") == 1_000_000
    assert await get_budget_field(registry, gone_id, "allocated") == 1_000_000

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM campaigns WHERE id = %s", (gone_id,))
        await conn.commit()

    await run_topup_cycle(pool, registry)

    assert await get_budget_field(registry, gone_id, "allocated") is None
    client = await registry.client_for_name(CLUSTER_NAME)
    from rtbdsp_common import keys as k

    assert await client.sismember(k.campaigns(CLUSTER_NAME), str(gone_id)) == 0
    assert await client.sismember(k.campaigns(CLUSTER_NAME), str(keep_id)) == 1
    assert await get_budget_field(registry, keep_id, "allocated") == 1_000_000


@pytest.mark.asyncio
async def test_paused_campaigns_not_topped_up(pool, registry, cluster_id) -> None:
    paused_id = await create_campaign(pool, name="paused-one", status="paused")
    await create_allocation(pool, paused_id, cluster_id, 1_000_000)

    await run_topup_cycle(pool, registry)

    assert await get_budget_field(registry, paused_id, "allocated") is None


@pytest.mark.asyncio
async def test_paused_campaign_budget_survives_pause(pool, registry, cluster_id) -> None:
    """A campaign topped up while active, then paused, keeps its Redis budget hash -- it must
    not be reaped, since it still has a live budget_allocations row (finding M9 boundary).
    """
    campaign_id = await create_campaign(pool, name="will-pause", status="active")
    await create_allocation(pool, campaign_id, cluster_id, 2_000_000)

    await run_topup_cycle(pool, registry)
    assert await get_budget_field(registry, campaign_id, "allocated") == 2_000_000

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("UPDATE campaigns SET status = 'paused' WHERE id = %s", (campaign_id,))
        await conn.commit()

    await run_topup_cycle(pool, registry)

    # still present -- not reaped
    assert await get_budget_field(registry, campaign_id, "allocated") == 2_000_000
