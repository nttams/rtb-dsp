"""Dashboard data: one `dashboard_snapshot.lua` call joined against Postgres (AC-24)."""

from __future__ import annotations

from rtbdsp_common import keys
from rtbdsp_common.redis_client import ClusterRegistry

FIELDS = ("allocated", "reserved", "spent", "bids", "wins", "losses", "nobid_budget", "expired", "clamped")


async def dashboard_rows(pool, registry: ClusterRegistry, cluster_name: str) -> list[dict]:
    entry = None
    for e in registry.all_entries():
        if e.name == cluster_name:
            entry = e
            break
    if entry is None:
        return []

    campaigns_key = keys.campaigns(cluster_name)
    flat = await entry.scripts["dashboard_snapshot"](keys=[campaigns_key], args=[cluster_name])

    redis_by_id: dict[int, dict] = {}
    for i in range(0, len(flat), 10):
        campaign_id = int(flat[i])
        values = flat[i + 1 : i + 10]
        redis_by_id[campaign_id] = {k: int(v) for k, v in zip(FIELDS, values)}

    if not redis_by_id:
        return []

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, name, bid_cpm_micros FROM campaigns WHERE id = ANY(%s)",
                (list(redis_by_id.keys()),),
            )
            pg_rows = await cur.fetchall()

    rows = []
    for campaign_id, name, bid_cpm_micros in pg_rows:
        r = redis_by_id.get(campaign_id)
        if r is None:
            continue
        remaining_micros = r["allocated"] - r["reserved"] - r["spent"]

        # A campaign is EXHAUSTED when it can no longer afford a single impression, not when
        # `remaining` hits exactly zero.
        #
        # Budget is consumed one impression at a time at `ceil(bid_cpm / 1000)` micros, so a
        # campaign almost always stops on a non-zero remainder smaller than that cost --
        # permanently unspendable dust. Measured on a real 90s run: 48 of 100 campaigns could
        # never bid again while every one of them still displayed a non-zero "remaining", and
        # a strict `remaining <= 0` test counted ZERO exhausted campaigns. That makes AC-36
        # ("campaigns that exhaust visibly stop bidding") unsatisfiable as literally written,
        # and it misleads the operator: the row looks live but the campaign is finished.
        per_imp_micros = -(-bid_cpm_micros // 1000)  # ceiling, matches money.cpm_to_per_imp
        exhausted = remaining_micros < per_imp_micros

        rows.append(
            {
                "campaign_id": campaign_id,
                "name": name,
                "bid_cpm": bid_cpm_micros / 1_000_000,
                "bids": r["bids"],
                "wins": r["wins"],
                "spend": r["spent"] / 1_000_000,
                "reserved": r["reserved"] / 1_000_000,
                "remaining": remaining_micros / 1_000_000,
                "per_imp_cost": per_imp_micros / 1_000_000,
                "exhausted": exhausted,
            }
        )
    rows.sort(key=lambda row: row["name"])
    return rows
