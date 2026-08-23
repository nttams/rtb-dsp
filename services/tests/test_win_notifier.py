"""win-notifier regressions: duplicate settle, late settle after reclaim, NOT_FOUND at HTTP
200, and the M1 implausible-price defense.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from tests.helpers import CLUSTER_NAME, create_allocation, create_campaign, do_reserve, do_topup, get_budget_field
from win_notifier.main import create_app


async def _client(pool, registry) -> httpx.AsyncClient:
    app = create_app(pool=pool, registry=registry)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_duplicate_win_settles_once(pool, registry, cluster_id) -> None:
    campaign_id = await create_campaign(pool, bid_cpm_micros=2_500_000, total_budget_micros=10_000_000)
    await create_allocation(pool, campaign_id, cluster_id, 10_000_000)
    await do_topup(registry, campaign_id, 10_000_000, 0)
    await do_reserve(registry, campaign_id, 2_500, "dup-resv")

    async with await _client(pool, registry) as client:
        payload = {
            "bid_id": "bid-1", "reservation_id": "dup-resv",
            "settled_price_micros_per_imp": 2_000, "cluster": CLUSTER_NAME,
        }
        r1 = await client.post("/win", json=payload)
        assert r1.status_code == 200
        body1 = r1.json()
        assert body1["ok"] is True
        assert body1["reason"] == "OK"

        r2 = await client.post("/win", json=payload)
        assert r2.status_code == 200
        body2 = r2.json()
        assert body2["ok"] is False
        assert body2["reason"] == "ALREADY_settled"

    assert await get_budget_field(registry, campaign_id, "spent") == 2_000


@pytest.mark.asyncio
async def test_unknown_reservation_returns_not_found_200(pool, registry, cluster_id) -> None:
    async with await _client(pool, registry) as client:
        r = await client.post(
            "/win",
            json={
                "bid_id": "bid-x", "reservation_id": "nope", "settled_price_micros_per_imp": 100,
                "cluster": CLUSTER_NAME,
            },
        )
        assert r.status_code == 200
        assert r.json() == {"ok": False, "reason": "NOT_FOUND", "settled_micros": 0}

        r2 = await client.post(
            "/loss", json={"bid_id": "bid-x", "reservation_id": "nope", "cluster": CLUSTER_NAME}
        )
        assert r2.status_code == 200
        assert r2.json() == {"ok": False, "reason": "NOT_FOUND", "released_micros": 0}


@pytest.mark.asyncio
async def test_late_settle_after_reclaim_still_records_spend(pool, registry, cluster_id) -> None:
    from tests.helpers import do_reclaim

    campaign_id = await create_campaign(pool, bid_cpm_micros=2_500_000, total_budget_micros=10_000_000)
    await create_allocation(pool, campaign_id, cluster_id, 10_000_000)
    await do_topup(registry, campaign_id, 10_000_000, 0)
    await do_reserve(registry, campaign_id, 2_500, "late-resv", ttl_ms=100)

    await asyncio.sleep(0.2)
    reclaimed_count, _ = await do_reclaim(registry)
    assert reclaimed_count >= 1
    assert await get_budget_field(registry, campaign_id, "reserved") == 0

    async with await _client(pool, registry) as client:
        r = await client.post(
            "/win",
            json={
                "bid_id": "bid-late", "reservation_id": "late-resv",
                "settled_price_micros_per_imp": 2_000, "cluster": CLUSTER_NAME,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["settled_micros"] == 2_000

    assert await get_budget_field(registry, campaign_id, "spent") == 2_000
    assert await get_budget_field(registry, campaign_id, "reserved") >= 0


@pytest.mark.asyncio
async def test_implausible_settled_price_rejected(pool, registry, cluster_id) -> None:
    campaign_id = await create_campaign(pool, bid_cpm_micros=2_500_000, total_budget_micros=10_000_000)
    await create_allocation(pool, campaign_id, cluster_id, 10_000_000)
    await do_topup(registry, campaign_id, 10_000_000, 0)
    await do_reserve(registry, campaign_id, 2_500, "implausible-resv")

    async with await _client(pool, registry) as client:
        r = await client.post(
            "/win",
            json={
                "bid_id": "bid-1", "reservation_id": "implausible-resv",
                # 2_500_000 is 1000x the reserved amount (2_500) -- a classic CPM/per-imp mixup.
                "settled_price_micros_per_imp": 2_500_000, "cluster": CLUSTER_NAME,
            },
        )
        assert r.status_code == 422

    # the reservation must be untouched -- still reserved, not settled
    assert await get_budget_field(registry, campaign_id, "reserved") == 2_500
