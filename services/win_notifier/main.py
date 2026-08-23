"""win-notifier — POST /win settles a reservation into confirmed spend, POST /loss releases it.

AC-17..AC-20.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field

from rtbdsp_common import keys, metrics
from rtbdsp_common.db import make_pool
from rtbdsp_common.redis_client import ClusterRegistry
from rtbdsp_common.settings import bind_host_port, get_settings

logger = logging.getLogger("win_notifier")

# Contract names per docs/metrics.md — the Grafana dashboard queries these exactly; a
# renamed metric renders as an empty panel, not an error.
wins_total = metrics.counter("rtbdsp_wins_total", "Settled wins")
losses_total = metrics.counter("rtbdsp_losses_total", "Released losses")
spend_micros_total = metrics.counter("rtbdsp_spend_micros_total", "Confirmed spend in micro-dollars")
settle_clamped_total = metrics.counter(
    "rtbdsp_settle_clamped_total",
    "settle.lua clamped the settled price -- non-zero means a CPM-vs-per-impression unit error",
)

# Internal-only (not in the Grafana contract), kept for operational visibility.
settle_attempts_total = metrics.counter(
    "win_notifier_settle_attempts_total", "settle.lua outcomes by reason", ("reason",)
)
release_attempts_total = metrics.counter(
    "win_notifier_release_attempts_total", "release.lua outcomes by reason", ("reason",)
)
pg_flush_errors_total = metrics.counter(
    "win_notifier_pg_flush_errors_total", "Postgres spend-flush failures after a successful settle"
)


class WinRequest(BaseModel):
    bid_id: str
    reservation_id: str
    # PER-IMPRESSION, not CPM (finding M1). OpenRTB bid.price is a CPM; the exchange divides
    # by 1000 before posting here.
    settled_price_micros_per_imp: int = Field(ge=0)
    cluster: str


class LossRequest(BaseModel):
    bid_id: str
    reservation_id: str
    cluster: str


@asynccontextmanager
async def _lifespan(app: FastAPI):
    settings = get_settings()
    if app.state.pool is None:
        app.state.pool = make_pool(settings.database_url)
        await app.state.pool.open()
    if app.state.registry is None:
        app.state.registry = ClusterRegistry(app.state.pool)
        await app.state.registry.refresh()
    try:
        yield
    finally:
        if app.state.registry is not None:
            await app.state.registry.close()
        if app.state.owns_pool and app.state.pool is not None:
            await app.state.pool.close()


def create_app(registry: ClusterRegistry | None = None, pool=None) -> FastAPI:
    app = FastAPI(title="win-notifier", lifespan=_lifespan)
    app.state.registry = registry
    app.state.pool = pool
    app.state.owns_pool = pool is None

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics_endpoint() -> Response:
        return Response(content=metrics.metrics_bytes(), media_type=metrics.CONTENT_TYPE_LATEST)

    @app.post("/win")
    async def win(req: WinRequest) -> dict:
        registry: ClusterRegistry = app.state.registry
        client = await registry.client_for_name(req.cluster)
        scripts = await registry.scripts_for_name(req.cluster)

        resv_key = keys.reservation(req.cluster, req.reservation_id)
        campaign_id_raw = await client.hget(resv_key, "campaign_id")
        if campaign_id_raw is None:
            settle_attempts_total.labels(reason="NOT_FOUND").inc()
            return {"ok": False, "reason": "NOT_FOUND", "settled_micros": 0}
        campaign_id = int(campaign_id_raw)
        amount_raw = await client.hget(resv_key, "amount")
        amount = int(amount_raw) if amount_raw is not None else 0

        # Finding M1 defense: settled_price_micros_per_imp is PER-IMPRESSION. A unit error
        # (e.g. posting a CPM) would otherwise be silently absorbed by settle.lua's min()
        # clamp, hiding a 1000x mistake. Reject anything implausibly large instead.
        if amount > 0 and req.settled_price_micros_per_imp > amount * 100:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"settled_price_micros_per_imp={req.settled_price_micros_per_imp} exceeds "
                    f"100x the reserved amount ({amount}); likely a CPM/per-impression unit error"
                ),
            )

        budget_key = keys.budget(req.cluster, campaign_id)
        zidx_key = keys.resv_index(req.cluster)
        stats_key = keys.stats(req.cluster, campaign_id)

        ok, reason, settled_micros = await scripts["settle"](
            keys=[resv_key, budget_key, zidx_key, stats_key],
            args=[req.reservation_id, req.settled_price_micros_per_imp, campaign_id, amount],
        )
        settle_attempts_total.labels(reason=reason).inc()

        if ok:
            wins_total.inc()
            spend_micros_total.inc(settled_micros)
            if reason == "OK_CLAMPED":
                settle_clamped_total.inc()
            pool = app.state.pool
            try:
                async with pool.connection() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            """
                            UPDATE budget_allocations
                            SET spent_micros = spent_micros + %s, updated_at = now()
                            WHERE campaign_id = %s
                              AND cluster_id = (SELECT id FROM clusters WHERE name = %s)
                            """,
                            (settled_micros, campaign_id, req.cluster),
                        )
            except Exception:
                # Do NOT roll back Redis: Redis is the in-flight authority. The topup worker's
                # RECONCILE_INTERVAL_SECS loop (GREATEST(pg, redis)) heals this later.
                logger.error(
                    "Postgres spend flush failed campaign_id=%s reservation_id=%s",
                    campaign_id,
                    req.reservation_id,
                    exc_info=True,
                )
                pg_flush_errors_total.inc()

        return {"ok": bool(ok), "reason": reason, "settled_micros": int(settled_micros)}

    @app.post("/loss")
    async def loss(req: LossRequest) -> dict:
        registry: ClusterRegistry = app.state.registry
        client = await registry.client_for_name(req.cluster)
        scripts = await registry.scripts_for_name(req.cluster)

        resv_key = keys.reservation(req.cluster, req.reservation_id)
        campaign_id_raw = await client.hget(resv_key, "campaign_id")
        if campaign_id_raw is None:
            release_attempts_total.labels(reason="NOT_FOUND").inc()
            return {"ok": False, "reason": "NOT_FOUND", "released_micros": 0}
        campaign_id = int(campaign_id_raw)

        budget_key = keys.budget(req.cluster, campaign_id)
        zidx_key = keys.resv_index(req.cluster)
        stats_key = keys.stats(req.cluster, campaign_id)

        ok, reason, released_micros = await scripts["release"](
            keys=[resv_key, budget_key, zidx_key, stats_key],
            args=[req.reservation_id, campaign_id],
        )
        release_attempts_total.labels(reason=reason).inc()
        if ok:
            losses_total.inc()
        return {"ok": bool(ok), "reason": reason, "released_micros": int(released_micros)}

    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    host, port = bind_host_port(settings.win_notifier_bind)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()
