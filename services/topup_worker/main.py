"""topup_worker — keeps Redis budget state in sync with Postgres.

Three independent loops per cluster:
  - topup (TOPUP_INTERVAL_SECS): Postgres allocated/spent -> topup.lua, then reap_deleted.lua.
  - reclaim (RECLAIM_INTERVAL_SECS): reclaim_expired.lua sweeps the resv_index ZSET.
  - reconcile (RECONCILE_INTERVAL_SECS): Postgres spent_micros = GREATEST(pg, redis).

Exposes GET /healthz + GET /metrics on TOPUP_HEALTH_PORT (finding M22). If this worker dies
silently, every campaign returns NO_BUDGET and the dashboard shows full budget with zero bids
-- the most expensive misdiagnosis available -- so health FAILS if either loop's last success
is older than 3 intervals.
"""

from __future__ import annotations

import asyncio
import logging
import time

import uvicorn
from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse

from rtbdsp_common import keys, metrics
from rtbdsp_common.db import make_pool
from rtbdsp_common.redis_client import ClusterRegistry
from rtbdsp_common.settings import get_settings

logger = logging.getLogger("topup_worker")

# Contract names per docs/metrics.md — the Grafana dashboard queries these exactly.
last_topup_success_gauge = metrics.gauge(
    "rtbdsp_topup_last_success_timestamp_seconds", "Unix seconds of the last successful topup cycle"
)
last_reclaim_success_gauge = metrics.gauge(
    "rtbdsp_reclaim_last_success_timestamp_seconds", "Unix seconds of the last successful reclaim cycle"
)
reclaimed_micros_total = metrics.counter(
    "rtbdsp_reclaimed_micros_total", "Budget returned to campaigns by expiry reclaim"
)
allocation_clamped_total = metrics.counter(
    "rtbdsp_allocation_clamped_total", "topup.lua floored allocated at reserved + spent"
)

# Internal-only (not in the Grafana contract), kept for operational visibility.
topup_cycles_total = metrics.counter("topup_worker_topup_cycles_total", "Completed topup cycles")
topup_errors_total = metrics.counter("topup_worker_topup_errors_total", "Failed topup cycles")
reclaim_cycles_total = metrics.counter("topup_worker_reclaim_cycles_total", "Completed reclaim cycles")
reclaim_errors_total = metrics.counter("topup_worker_reclaim_errors_total", "Failed reclaim cycles")
reconcile_errors_total = metrics.counter("topup_worker_reconcile_errors_total", "Failed reconcile cycles")
reaped_total = metrics.counter("topup_worker_reaped_campaigns_total", "Orphaned campaigns reaped from Redis")


class WorkerState:
    def __init__(self) -> None:
        self.last_topup_success: float | None = None
        self.last_reclaim_success: float | None = None


async def _fetch_active_allocations(pool, cluster_id: int) -> list[tuple[int, int, int]]:
    """Active campaigns only (paused campaigns are not topped up — finding in Step 4)."""
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
            return await cur.fetchall()


async def _fetch_live_campaign_ids(pool, cluster_id: int) -> list[int]:
    """Every campaign with a live allocation row for this cluster, active OR paused.

    Deliberately NOT filtered by status: a paused campaign still has a budget_allocations
    row and must NOT be reaped from Redis (its budget hash is real, just not being topped
    up). Only a genuinely deleted campaign — whose allocation row is gone via CASCADE —
    is absent from this list.
    """
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT campaign_id FROM budget_allocations WHERE cluster_id = %s",
                (cluster_id,),
            )
            rows = await cur.fetchall()
            return [r[0] for r in rows]


async def run_topup_cycle(pool, registry: ClusterRegistry) -> None:
    for entry in registry.all_entries():
        rows = await _fetch_active_allocations(pool, entry.id)
        campaigns_key = keys.campaigns(entry.name)

        if rows:
            pipe = entry.client.pipeline(transaction=False)
            for campaign_id, allocated_micros, spent_micros in rows:
                budget_key = keys.budget(entry.name, campaign_id)
                # AsyncScript.__call__ is itself a coroutine (it awaits client.evalsha()
                # internally) even when only queuing into a pipeline, so this must be
                # awaited to actually stage the command.
                await entry.scripts["topup"](
                    keys=[budget_key, campaigns_key],
                    args=[campaign_id, allocated_micros, spent_micros],
                    client=pipe,
                )
            results = await pipe.execute()
            # Each result is topup.lua's {allocated, reserved, spent, clamped}.
            for result in results:
                if result and int(result[3]) == 1:
                    allocation_clamped_total.inc()

        live_ids = await _fetch_live_campaign_ids(pool, entry.id)
        reaped = await entry.scripts["reap_deleted"](
            keys=[campaigns_key],
            args=[entry.name, *[str(i) for i in live_ids]],
        )
        if reaped:
            reaped_total.inc(reaped)


async def run_reclaim_cycle(registry: ClusterRegistry, batch_limit: int = 500) -> None:
    for entry in registry.all_entries():
        zidx_key = keys.resv_index(entry.name)
        count, total_micros = await entry.scripts["reclaim_expired"](
            keys=[zidx_key], args=[batch_limit, entry.name]
        )
        if total_micros:
            reclaimed_micros_total.inc(total_micros)


async def run_reconcile_cycle(pool, registry: ClusterRegistry) -> None:
    for entry in registry.all_entries():
        campaigns_key = keys.campaigns(entry.name)
        campaign_ids = await entry.client.smembers(campaigns_key)
        for cid_str in campaign_ids:
            budget_key = keys.budget(entry.name, cid_str)
            spent_raw = await entry.client.hget(budget_key, "spent")
            spent = int(spent_raw) if spent_raw is not None else 0
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        UPDATE budget_allocations
                        SET spent_micros = GREATEST(spent_micros, %s), updated_at = now()
                        WHERE campaign_id = %s AND cluster_id = %s
                        """,
                        (spent, int(cid_str), entry.id),
                    )


def create_health_app(state: WorkerState) -> FastAPI:
    app = FastAPI(title="topup-worker")

    @app.get("/healthz")
    async def healthz() -> Response:
        settings = get_settings()
        now = time.time()
        topup_ok = (
            state.last_topup_success is not None
            and (now - state.last_topup_success) < 3 * settings.topup_interval_secs
        )
        reclaim_ok = (
            state.last_reclaim_success is not None
            and (now - state.last_reclaim_success) < 3 * settings.reclaim_interval_secs
        )
        payload = {
            "status": "ok" if (topup_ok and reclaim_ok) else "unhealthy",
            "last_topup_success_ts": state.last_topup_success,
            "last_reclaim_success_ts": state.last_reclaim_success,
        }
        if not (topup_ok and reclaim_ok):
            return JSONResponse(status_code=503, content=payload)
        return JSONResponse(status_code=200, content=payload)

    @app.get("/metrics")
    async def metrics_endpoint() -> Response:
        if state.last_topup_success is not None:
            last_topup_success_gauge.set(state.last_topup_success)
        if state.last_reclaim_success is not None:
            last_reclaim_success_gauge.set(state.last_reclaim_success)
        return Response(content=metrics.metrics_bytes(), media_type=metrics.CONTENT_TYPE_LATEST)

    return app


async def _main() -> None:
    settings = get_settings()
    pool = make_pool(settings.database_url)
    await pool.open()
    registry = ClusterRegistry(pool)
    await registry.refresh()
    state = WorkerState()
    app = create_health_app(state)
    config = uvicorn.Config(app, host="0.0.0.0", port=settings.topup_health_port, log_level="warning")
    server = uvicorn.Server(config)

    async def topup_loop() -> None:
        while True:
            try:
                await run_topup_cycle(pool, registry)
                state.last_topup_success = time.time()
                topup_cycles_total.inc()
            except Exception:
                logger.exception("topup cycle failed")
                topup_errors_total.inc()
            await asyncio.sleep(settings.topup_interval_secs)

    async def reclaim_loop() -> None:
        while True:
            try:
                await run_reclaim_cycle(registry)
                state.last_reclaim_success = time.time()
                reclaim_cycles_total.inc()
            except Exception:
                logger.exception("reclaim cycle failed")
                reclaim_errors_total.inc()
            await asyncio.sleep(settings.reclaim_interval_secs)

    async def reconcile_loop() -> None:
        while True:
            try:
                await run_reconcile_cycle(pool, registry)
            except Exception:
                logger.exception("reconcile cycle failed")
                reconcile_errors_total.inc()
            await asyncio.sleep(settings.reconcile_interval_secs)

    await asyncio.gather(server.serve(), topup_loop(), reclaim_loop(), reconcile_loop())


def run() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    run()
