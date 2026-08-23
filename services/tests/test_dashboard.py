"""GET /dashboard: one dashboard_snapshot.lua call joined against Postgres (AC-24), auto-
refresh meta tag (AC-25), and zero-remaining rows visually distinct (AC-36).
"""

from __future__ import annotations

import httpx
import pytest

from campaign_app.main import create_app
from tests.helpers import create_allocation, create_campaign, do_reserve, do_settle
from topup_worker.main import run_topup_cycle


@pytest.mark.asyncio
async def test_dashboard_meta_refresh_and_exhausted_row(pool, registry, cluster_id) -> None:
    campaign_id = await create_campaign(
        pool, name="dash-test", bid_cpm_micros=1_000_000, total_budget_micros=1_000
    )
    await create_allocation(pool, campaign_id, cluster_id, 1_000)
    await run_topup_cycle(pool, registry)

    reserve_result = await do_reserve(registry, campaign_id, 1_000, "dash-resv")
    assert int(reserve_result[0]) == 1
    await do_settle(registry, campaign_id, "dash-resv", 1_000, 1_000)

    app = create_app(pool=pool, registry=registry)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/dashboard")
        assert r.status_code == 200
        assert 'http-equiv="refresh"' in r.text
        assert "dash-test" in r.text
        assert "exhausted" in r.text


@pytest.mark.asyncio
async def test_dashboard_columns(pool, registry, cluster_id) -> None:
    campaign_id = await create_campaign(
        pool, name="dash-columns", bid_cpm_micros=2_500_000, total_budget_micros=1_000_000
    )
    await create_allocation(pool, campaign_id, cluster_id, 1_000_000)
    await run_topup_cycle(pool, registry)
    await do_reserve(registry, campaign_id, 2_500, "dash-columns-resv")

    from campaign_app.stats import dashboard_rows

    rows = await dashboard_rows(pool, registry, "us-east")
    row = next(r for r in rows if r["name"] == "dash-columns")
    assert row["bids"] == 1
    assert row["reserved"] == pytest.approx(0.0025)
    assert row["remaining"] == pytest.approx(1.0 - 0.0025)
    assert row["exhausted"] is False
