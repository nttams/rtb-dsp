"""100-campaign demo seeder (AC-26..AC-28).

Budget sizing (finding M10): allocated_micros = impressions_target * cpm_to_per_imp_micros
(bid_cpm), with impressions_target drawn from [SEED_IMPRESSIONS_MIN, SEED_IMPRESSIONS_MAX].
CPM is DELIBERATELY correlated with budget this way -- independent draws made "at least one
campaign exhausts" flaky by construction, since highest-CPM-wins concentrates traffic on the
top-CPM campaign, which only exhausts if it happens to have a small budget.
"""

from __future__ import annotations

import random

from rtbdsp_common import keys
from rtbdsp_common.money import cpm_to_per_imp_micros
from rtbdsp_common.redis_client import ClusterRegistry

# ISO-3166-1-alpha-3, matching the wire format (OpenRTB 2.6 3.2.19) so what's stored in
# Postgres matches what the mock exchange sends. Alpha-2 still matches via the Rust engine's
# filters::country::to_alpha3 normalization, but alpha-3 is canonical.
COUNTRIES = ["USA", "GBR", "DEU", "FRA", "JPN", "BRA"]
LANGUAGES = ["en", "de", "fr", "ja", "pt"]
AD_TYPES = ["banner", "video", "native"]
SIZES = [(300, 250), (728, 90), (320, 50), (160, 600), (970, 250), (300, 600)]

N_CAMPAIGNS = 100
UNTARGETED_FRACTION = 0.20
EXCLUDE_FRACTION = 0.15


def _find_entry(registry: ClusterRegistry, cluster_name: str):
    for e in registry.all_entries():
        if e.name == cluster_name:
            return e
    return None


async def reset_demo_state(pool, registry: ClusterRegistry, cluster_name: str) -> list[int]:
    """Idempotent reset: delete prior demo campaigns (CASCADE removes creatives and
    allocations) in one transaction, then clear the matching Redis state, including
    `resv_index` and every outstanding reservation hash for this cluster (finding M5) --
    leaving those populated across a reseed is what lets a later reclaim drive `reserved`
    negative on a recreated hash.
    """
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT id FROM campaigns WHERE demo_batch = 'demo'")
            old_ids = [r[0] for r in await cur.fetchall()]
            await cur.execute("DELETE FROM campaigns WHERE demo_batch = 'demo'")
        await conn.commit()

    entry = _find_entry(registry, cluster_name)
    if entry is not None:
        client = entry.client
        if old_ids:
            pipe = client.pipeline(transaction=False)
            for cid in old_ids:
                pipe.delete(keys.budget(cluster_name, cid))
                pipe.delete(keys.stats(cluster_name, cid))
                pipe.srem(keys.campaigns(cluster_name), str(cid))
            await pipe.execute()

        zidx_key = keys.resv_index(cluster_name)
        members = await client.zrange(zidx_key, 0, -1)
        if members:
            pipe2 = client.pipeline(transaction=False)
            for m in members:
                parsed = keys.parse_member(m)
                if parsed:
                    rid, _cid, _amt = parsed
                    pipe2.delete(keys.reservation(cluster_name, rid))
            pipe2.delete(zidx_key)
            await pipe2.execute()

    return old_ids


def _gen_campaign(rng: random.Random, idx: int, seed_min: int, seed_max: int) -> dict:
    bid_cpm_dollars = round(rng.uniform(0.50, 8.00), 4)
    bid_cpm_micros = int(round(bid_cpm_dollars * 1_000_000))

    if rng.random() < UNTARGETED_FRACTION:
        include_countries: list[str] = []
        include_languages: list[str] = []
    else:
        include_countries = rng.sample(COUNTRIES, k=rng.randint(1, 3))
        include_languages = rng.sample(LANGUAGES, k=rng.randint(1, 2))

    exclude_countries: list[str] = []
    if rng.random() < EXCLUDE_FRACTION:
        candidates = [c for c in COUNTRIES if c not in include_countries] or COUNTRIES
        exclude_countries = [rng.choice(candidates)]

    impressions_target = rng.randint(seed_min, seed_max)
    per_imp_micros = cpm_to_per_imp_micros(bid_cpm_micros)
    total_budget_micros = impressions_target * per_imp_micros

    ad_type = rng.choice(AD_TYPES)
    width, height = rng.choice(SIZES)

    return {
        "name": f"demo-campaign-{idx:03d}",
        "bid_cpm_micros": bid_cpm_micros,
        "total_budget_micros": total_budget_micros,
        "include_countries": include_countries,
        "exclude_countries": exclude_countries,
        "include_languages": include_languages,
        "exclude_languages": [],
        "ad_type": ad_type,
        "width": width,
        "height": height,
    }


async def seed_demo(
    pool,
    registry: ClusterRegistry,
    cluster_id: int,
    cluster_name: str,
    *,
    seed_min: int,
    seed_max: int,
    rng_seed: int | None = None,
) -> int:
    rng = random.Random(rng_seed)
    await reset_demo_state(pool, registry, cluster_name)

    specs = [_gen_campaign(rng, i, seed_min, seed_max) for i in range(1, N_CAMPAIGNS + 1)]

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            for c in specs:
                await cur.execute(
                    """
                    INSERT INTO campaigns (name, status, bid_cpm_micros, total_budget_micros,
                                            include_countries, exclude_countries,
                                            include_languages, exclude_languages, demo_batch)
                    VALUES (%s, 'active', %s, %s, %s, %s, %s, %s, 'demo')
                    RETURNING id
                    """,
                    (
                        c["name"],
                        c["bid_cpm_micros"],
                        c["total_budget_micros"],
                        c["include_countries"],
                        c["exclude_countries"],
                        c["include_languages"],
                        c["exclude_languages"],
                    ),
                )
                campaign_id = (await cur.fetchone())[0]

                await cur.execute(
                    """
                    INSERT INTO creatives (campaign_id, name, ad_type, width, height)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (campaign_id, f"{c['name']}-creative", c["ad_type"], c["width"], c["height"]),
                )

                await cur.execute(
                    """
                    INSERT INTO budget_allocations (campaign_id, cluster_id, allocated_micros, spent_micros)
                    VALUES (%s, %s, %s, 0)
                    """,
                    (campaign_id, cluster_id, c["total_budget_micros"]),
                )
        await conn.commit()

    # Prime this cluster's Redis immediately instead of waiting for the topup worker's next
    # tick.
    #
    # `reset_demo_state` clears the cluster's campaign set, and the dashboard renders from
    # Redis (`dashboard_snapshot.lua` over that set). Without this the dashboard is BLANK for
    # up to TOPUP_INTERVAL_SECS after seeding — on a demo where you click "seed 100 campaigns"
    # and look straight at the dashboard, an empty table reads as "seeding failed".
    #
    # `topup.lua` is idempotent (it floors `allocated` at `reserved + spent` and only seeds
    # `spent` on hash creation), so doing it here and again on the worker's next tick is safe
    # and cannot double-credit.
    await _prime_redis_budgets(pool, registry, cluster_id, cluster_name)

    return len(specs)


async def _prime_redis_budgets(pool, registry: ClusterRegistry, cluster_id: int, cluster_name: str) -> None:
    """Apply topup.lua for every allocation in this cluster, exactly as the worker would."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT ba.campaign_id, ba.allocated_micros, ba.spent_micros
                FROM budget_allocations ba
                JOIN campaigns c ON c.id = ba.campaign_id
                WHERE ba.cluster_id = %s AND c.status = 'active'
                """,
                (cluster_id,),
            )
            rows = await cur.fetchall()

    client = await registry.client_for(cluster_id)
    scripts = await registry.scripts_for(cluster_id)
    campaigns_key = keys.campaigns(cluster_name)
    pipe = client.pipeline(transaction=False)
    for campaign_id, allocated_micros, spent_micros in rows:
        await scripts["topup"](
            keys=[keys.budget(cluster_name, campaign_id), campaigns_key],
            args=[campaign_id, allocated_micros, spent_micros],
            client=pipe,
        )
    await pipe.execute()
