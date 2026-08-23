"""Shared test helpers: campaign/allocation creation and direct Lua script invocation."""

from __future__ import annotations

from rtbdsp_common import keys
from rtbdsp_common.redis_client import ClusterRegistry

CLUSTER_NAME = "us-east"


async def create_campaign(
    pool,
    *,
    name: str = "test-campaign",
    status: str = "active",
    bid_cpm_micros: int = 2_500_000,
    total_budget_micros: int = 1_000_000,
    include_countries: list[str] | None = None,
    exclude_countries: list[str] | None = None,
    include_languages: list[str] | None = None,
    exclude_languages: list[str] | None = None,
) -> int:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO campaigns (name, status, bid_cpm_micros, total_budget_micros,
                                        include_countries, exclude_countries,
                                        include_languages, exclude_languages)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    name, status, bid_cpm_micros, total_budget_micros,
                    include_countries or [], exclude_countries or [],
                    include_languages or [], exclude_languages or [],
                ),
            )
            campaign_id = (await cur.fetchone())[0]
        await conn.commit()
    return campaign_id


async def create_allocation(pool, campaign_id: int, cluster_id: int, allocated_micros: int, spent_micros: int = 0) -> None:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO budget_allocations (campaign_id, cluster_id, allocated_micros, spent_micros)
                VALUES (%s, %s, %s, %s)
                """,
                (campaign_id, cluster_id, allocated_micros, spent_micros),
            )
        await conn.commit()


async def do_reserve(
    registry: ClusterRegistry, campaign_id: int, amount_micros: int, reservation_id: str,
    bid_id: str = "bid-1", ttl_ms: int = 5000, cluster_name: str = CLUSTER_NAME,
):
    scripts = await registry.scripts_for_name(cluster_name)
    budget_key = keys.budget(cluster_name, campaign_id)
    resv_key = keys.reservation(cluster_name, reservation_id)
    zidx_key = keys.resv_index(cluster_name)
    stats_key = keys.stats(cluster_name, campaign_id)
    return await scripts["reserve"](
        keys=[budget_key, resv_key, zidx_key, stats_key],
        args=[amount_micros, reservation_id, bid_id, campaign_id, ttl_ms],
    )


async def do_settle(
    registry: ClusterRegistry, campaign_id: int, reservation_id: str, settled_price_micros_per_imp: int,
    amount_micros: int = 0, cluster_name: str = CLUSTER_NAME,
):
    scripts = await registry.scripts_for_name(cluster_name)
    resv_key = keys.reservation(cluster_name, reservation_id)
    budget_key = keys.budget(cluster_name, campaign_id)
    zidx_key = keys.resv_index(cluster_name)
    stats_key = keys.stats(cluster_name, campaign_id)
    return await scripts["settle"](
        keys=[resv_key, budget_key, zidx_key, stats_key],
        args=[reservation_id, settled_price_micros_per_imp, campaign_id, amount_micros],
    )


async def do_release(
    registry: ClusterRegistry, campaign_id: int, reservation_id: str, cluster_name: str = CLUSTER_NAME,
):
    scripts = await registry.scripts_for_name(cluster_name)
    resv_key = keys.reservation(cluster_name, reservation_id)
    budget_key = keys.budget(cluster_name, campaign_id)
    zidx_key = keys.resv_index(cluster_name)
    stats_key = keys.stats(cluster_name, campaign_id)
    return await scripts["release"](
        keys=[resv_key, budget_key, zidx_key, stats_key],
        args=[reservation_id, campaign_id],
    )


async def do_topup(
    registry: ClusterRegistry, campaign_id: int, allocated_micros: int, pg_spent_micros: int,
    cluster_name: str = CLUSTER_NAME,
):
    scripts = await registry.scripts_for_name(cluster_name)
    budget_key = keys.budget(cluster_name, campaign_id)
    campaigns_key = keys.campaigns(cluster_name)
    return await scripts["topup"](
        keys=[budget_key, campaigns_key],
        args=[campaign_id, allocated_micros, pg_spent_micros],
    )


async def do_reclaim(registry: ClusterRegistry, batch_limit: int = 500, cluster_name: str = CLUSTER_NAME):
    scripts = await registry.scripts_for_name(cluster_name)
    zidx_key = keys.resv_index(cluster_name)
    return await scripts["reclaim_expired"](keys=[zidx_key], args=[batch_limit, cluster_name])


async def get_budget_field(registry: ClusterRegistry, campaign_id: int, field: str, cluster_name: str = CLUSTER_NAME):
    client = await registry.client_for_name(cluster_name)
    val = await client.hget(keys.budget(cluster_name, campaign_id), field)
    return int(val) if val is not None else None


async def get_stats_field(registry: ClusterRegistry, campaign_id: int, field: str, cluster_name: str = CLUSTER_NAME):
    client = await registry.client_for_name(cluster_name)
    val = await client.hget(keys.stats(cluster_name, campaign_id), field)
    return int(val) if val is not None else None
