"""Finding B1: spend 90% of a budget, FLUSHDB, one topup cycle -> Redis spent equals Postgres
spent_micros and remaining available is 10%, not 100%.
"""

from __future__ import annotations

import pytest
import redis.asyncio as aioredis

from tests.helpers import CLUSTER_NAME, create_allocation, create_campaign, do_reserve, do_settle, get_budget_field
from topup_worker.main import run_topup_cycle


@pytest.mark.asyncio
async def test_flushdb_then_topup_restores_spend_from_postgres(pool, registry, cluster_id, redis_url: str) -> None:
    allocated_micros = 10_000_000
    campaign_id = await create_campaign(pool, name="flush-test")
    await create_allocation(pool, campaign_id, cluster_id, allocated_micros)

    await run_topup_cycle(pool, registry)

    # spend 90%
    spend = 9_000_000
    reserve_result = await do_reserve(registry, campaign_id, spend, "r-flush")
    assert int(reserve_result[0]) == 1
    settle_result = await do_settle(registry, campaign_id, "r-flush", spend)
    assert int(settle_result[0]) == 1
    assert await get_budget_field(registry, campaign_id, "spent") == spend

    # the win notifier would have flushed this to Postgres on a successful settle
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE budget_allocations SET spent_micros = %s WHERE campaign_id = %s AND cluster_id = %s",
                (spend, campaign_id, cluster_id),
            )
        await conn.commit()

    # simulate a Redis restart / flush
    r = aioredis.from_url(redis_url, decode_responses=True)
    await r.flushdb()
    await r.aclose()

    assert await get_budget_field(registry, campaign_id, "spent") is None  # gone after flush

    await run_topup_cycle(pool, registry)

    redis_spent = await get_budget_field(registry, campaign_id, "spent")
    redis_reserved = await get_budget_field(registry, campaign_id, "reserved")
    redis_allocated = await get_budget_field(registry, campaign_id, "allocated")

    assert redis_spent == spend
    assert redis_reserved == 0
    remaining = redis_allocated - redis_reserved - redis_spent
    assert remaining == allocated_micros - spend  # 10%, not 100%
