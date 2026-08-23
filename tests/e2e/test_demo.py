"""AC-34..AC-38 -- full demo run against a live stack.

Requires the Rust engine, campaign-app, and win-notifier (typically via `make demo`) to be
reachable. The engine and deploy/docker-compose.yml are owned outside this task's scope
(services/ + tests/e2e/ only), so this module skips loudly rather than failing the suite when
the stack is not up -- it is a real acceptance check for whoever runs the full stack, not a
unit test.
"""

from __future__ import annotations

import argparse
import asyncio
import os

import httpx
import pytest

ENGINE_URL = os.environ.get("ENGINE_URL", "http://localhost:8080")
CAMPAIGN_APP_URL = os.environ.get("CAMPAIGN_APP_URL", "http://localhost:8000")
WIN_NOTIFIER_URL = os.environ.get("WIN_NOTIFIER_URL", "http://localhost:8010")
DATABASE_URL = os.environ.get("DATABASE_URL")


def _stack_reachable() -> bool:
    try:
        for url in (f"{ENGINE_URL}/healthz", f"{CAMPAIGN_APP_URL}/healthz", f"{WIN_NOTIFIER_URL}/healthz"):
            resp = httpx.get(url, timeout=1.5)
            if resp.status_code >= 500:
                return False
        return True
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(
    not _stack_reachable(),
    reason=(
        "full demo stack (engine, campaign-app, win-notifier) not reachable at "
        f"{ENGINE_URL}/{CAMPAIGN_APP_URL}/{WIN_NOTIFIER_URL} -- start it (e.g. `make demo`) "
        "before running tests/e2e"
    ),
)


def test_seed_then_dashboard_shows_campaigns() -> None:
    """AC-26, AC-35: seeding populates the dashboard."""
    r = httpx.post(f"{CAMPAIGN_APP_URL}/demo/seed", timeout=30.0)
    assert r.status_code in (200, 303)

    r = httpx.get(f"{CAMPAIGN_APP_URL}/dashboard", timeout=10.0)
    assert r.status_code == 200
    assert "demo-campaign-" in r.text


def test_short_exchange_run_produces_spend_and_no_overspend() -> None:
    """AC-35, AC-36, AC-37: run the mock exchange briefly against the live engine, then
    assert spend climbed and no campaign's confirmed spend exceeded its allocation.
    """
    from mock_exchange.main import run as run_exchange

    args = argparse.Namespace(
        rate=50, duration=5, win_rate=0.5, silent_loss_pct=5.0, seed=1234, engine=ENGINE_URL,
    )
    stats = asyncio.run(run_exchange(args, WIN_NOTIFIER_URL))
    assert stats["requests"] > 0
    assert stats["bids"] > 0, "no bids at all -- seed the demo campaigns first"

    r = httpx.get(f"{CAMPAIGN_APP_URL}/dashboard", timeout=10.0)
    assert r.status_code == 200

    if not DATABASE_URL:
        pytest.skip("DATABASE_URL not set -- cannot verify the no-overspend invariant directly")

    import psycopg

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT campaign_id, allocated_micros, spent_micros FROM budget_allocations "
                "WHERE spent_micros > allocated_micros"
            )
            overspent = cur.fetchall()
    assert overspent == [], f"AC-37 violated: {overspent}"


@pytest.mark.skipif(not _stack_reachable(), reason="live stack not reachable")
def test_ac36_campaigns_exhaust_and_stop_bidding() -> None:
    """AC-36: campaigns that exhaust their budget visibly stop bidding.

    EXHAUSTION IS NOT ``remaining == 0``. Budget is consumed one impression at a time at
    ``ceil(bid_cpm / 1000)`` micros, so a campaign nearly always stops on a non-zero remainder
    smaller than one impression's cost -- unspendable dust. Measured on a real 90s run at
    rate=200: 48 of 100 campaigns could never bid again while every one still displayed a
    non-zero ``remaining``, and a strict ``remaining <= 0`` test found ZERO exhausted
    campaigns. Asserting the literal wording would make this criterion unsatisfiable.

    The engine's ``no_budget`` no-bid counter is the independent confirmation: it is
    incremented by ``reserve.lua`` returning INSUFFICIENT/NO_BUDGET, so a non-zero value
    proves campaigns actually stopped bidding rather than merely looking depleted.
    """
    if not DATABASE_URL:
        pytest.skip("DATABASE_URL not set -- cannot compute per-impression cost")

    import psycopg

    # Drive our OWN exchange run rather than relying on state from earlier tests: the seeding
    # test in this module resets every campaign's spend, so a shared-state assumption makes
    # this criterion pass or fail depending on test ORDER. Volume is sized from the same
    # measured arithmetic as SEED_IMPRESSIONS_MIN/MAX -- ~2,500 wins against ~22 impressions of
    # average budget across 100 campaigns is comfortably past the exhaustion cliff.
    from mock_exchange.main import run as run_exchange

    args = argparse.Namespace(
        rate=200, duration=45, win_rate=0.5, silent_loss_pct=5.0, seed=99, engine=ENGINE_URL,
    )
    stats = asyncio.run(run_exchange(args, WIN_NOTIFIER_URL))
    assert stats["wins"] > 0, "no wins -- cannot assess exhaustion"

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id,
                       ceil(c.bid_cpm_micros / 1000.0)::bigint AS per_imp_micros,
                       ba.allocated_micros,
                       ba.spent_micros
                FROM campaigns c
                JOIN budget_allocations ba ON ba.campaign_id = c.id
                """
            )
            rows = cur.fetchall()

    if not rows:
        pytest.skip("no campaigns seeded")

    # Postgres holds confirmed spend; reserved is Redis-side and in-flight, so this is a
    # lower bound on exhaustion -- good enough for an assertion that wants "at least N".
    exhausted = [cid for cid, per_imp, alloc, spent in rows if (alloc - spent) < per_imp]

    metrics = httpx.get(f"{ENGINE_URL}/metrics", timeout=10.0).text
    no_budget = 0
    for line in metrics.splitlines():
        if line.startswith('rtbdsp_nobid_total{reason="no_budget"}'):
            no_budget = int(float(line.rsplit(" ", 1)[1]))

    assert exhausted, (
        "AC-36: no campaign exhausted. If spend is climbing but nothing exhausts, the seeded "
        "impression targets are too large for the run length -- see SEED_IMPRESSIONS_MIN/MAX "
        "in .env.example, which are sized from a measured run."
    )
    assert no_budget > 0, (
        f"{len(exhausted)} campaigns are out of budget but the engine never recorded a "
        "no_budget no-bid; exhausted campaigns are not actually being stopped"
    )
