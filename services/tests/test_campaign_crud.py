"""Campaign + creative CRUD round-trips through campaign_app's HTTP surface (AC-21, AC-22),
and the SUM(allocations) <= total_budget invariant (a read-check-write under SELECT ... FOR
UPDATE) is enforced on write.
"""

from __future__ import annotations

import httpx
import pytest

from campaign_app.main import create_app


async def _client(pool, registry) -> httpx.AsyncClient:
    app = create_app(pool=pool, registry=registry)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_campaign_create_list_edit_delete(pool, registry, cluster_id) -> None:
    async with await _client(pool, registry) as client:
        r = await client.post(
            "/campaigns",
            data={
                "name": "crud-test", "status": "active", "bid_cpm": "2.50",
                "total_budget": "10.00", "allocated_budget": "10.00",
                "include_countries": "USA,GBR", "exclude_countries": "",
                "include_languages": "en", "exclude_languages": "",
            },
        )
        assert r.status_code == 303

        r = await client.get("/campaigns")
        assert r.status_code == 200
        assert "crud-test" in r.text

        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT id FROM campaigns WHERE name = 'crud-test'")
                campaign_id = (await cur.fetchone())[0]

        r = await client.get(f"/campaigns/{campaign_id}/edit")
        assert r.status_code == 200
        assert "crud-test" in r.text

        r = await client.post(
            f"/campaigns/{campaign_id}",
            data={
                "name": "crud-test-renamed", "status": "paused", "bid_cpm": "3.00",
                "total_budget": "10.00", "allocated_budget": "10.00",
                "include_countries": "USA", "exclude_countries": "",
                "include_languages": "en", "exclude_languages": "",
            },
        )
        assert r.status_code == 303

        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT name, status FROM campaigns WHERE id = %s", (campaign_id,))
                name, status = await cur.fetchone()
        assert name == "crud-test-renamed"
        assert status == "paused"

        r = await client.get(f"/campaigns/{campaign_id}/creatives/new")
        assert r.status_code == 200

        r = await client.post(
            f"/campaigns/{campaign_id}/creatives",
            data={"name": "cr-1", "ad_type": "banner", "width": "300", "height": "250",
                  "include_countries": "", "include_languages": ""},
        )
        assert r.status_code == 303

        r = await client.get(f"/campaigns/{campaign_id}/creatives")
        assert r.status_code == 200
        assert "cr-1" in r.text

        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT id FROM creatives WHERE campaign_id = %s", (campaign_id,))
                creative_id = (await cur.fetchone())[0]

        r = await client.post(
            f"/campaigns/{campaign_id}/creatives/{creative_id}/delete"
        )
        assert r.status_code == 303

        r = await client.post(f"/campaigns/{campaign_id}/delete")
        assert r.status_code == 303

        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT count(*) FROM campaigns WHERE id = %s", (campaign_id,))
                assert (await cur.fetchone())[0] == 0


@pytest.mark.asyncio
async def test_allocation_cannot_exceed_total_budget(pool, registry, cluster_id) -> None:
    async with await _client(pool, registry) as client:
        r = await client.post(
            "/campaigns",
            data={
                "name": "budget-guard", "status": "active", "bid_cpm": "1.00",
                "total_budget": "5.00", "allocated_budget": "10.00",
                "include_countries": "", "exclude_countries": "",
                "include_languages": "", "exclude_languages": "",
            },
        )
        # allocated (10) > total_budget (5) -- rejected, not silently accepted
        assert r.status_code == 400

        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT count(*) FROM campaigns WHERE name = 'budget-guard'")
                assert (await cur.fetchone())[0] == 0
