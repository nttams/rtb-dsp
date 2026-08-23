"""AC-40 + docs/metrics.md contract: each Python service exposes GET /metrics with the exact
names the (committed) Grafana dashboard queries. A renamed metric renders as an empty panel,
not an error, so this test pins the names.
"""

from __future__ import annotations

import httpx
import pytest

from campaign_app.main import create_app as create_campaign_app
from topup_worker.main import WorkerState, create_health_app
from win_notifier.main import create_app as create_win_notifier_app


@pytest.mark.asyncio
async def test_win_notifier_metrics_contract(pool, registry) -> None:
    app = create_win_notifier_app(pool=pool, registry=registry)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/metrics")
        assert r.status_code == 200
        body = r.text
        for name in (
            "rtbdsp_wins_total", "rtbdsp_losses_total",
            "rtbdsp_spend_micros_total", "rtbdsp_settle_clamped_total",
        ):
            assert name in body, f"missing contract metric {name}"


@pytest.mark.asyncio
async def test_topup_worker_metrics_contract() -> None:
    app = create_health_app(WorkerState())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/metrics")
        assert r.status_code == 200
        body = r.text
        for name in (
            "rtbdsp_topup_last_success_timestamp_seconds",
            "rtbdsp_reclaim_last_success_timestamp_seconds",
            "rtbdsp_reclaimed_micros_total",
            "rtbdsp_allocation_clamped_total",
        ):
            assert name in body, f"missing contract metric {name}"


@pytest.mark.asyncio
async def test_topup_worker_healthz_fails_when_stale() -> None:
    app = create_health_app(WorkerState())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/healthz")
        assert r.status_code == 503  # never succeeded yet


@pytest.mark.asyncio
async def test_campaign_app_metrics_contract(pool, registry) -> None:
    app = create_campaign_app(pool=pool, registry=registry)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/metrics")
        assert r.status_code == 200
        assert "rtbdsp_budget_exhausted_campaigns" in r.text
