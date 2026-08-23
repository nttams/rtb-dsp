"""A second-price clearing price below our bid settles strictly below the reserved amount
(finding M1 -- with the pre-fix naming, settle.lua's min() clamp made every win settle at
exactly the reserved amount, so the auction had no observable effect).
"""

from __future__ import annotations

import random

import pytest

from mock_exchange.main import make_bid_request, simulate_second_price
from rtbdsp_common.money import cpm_to_per_imp_micros
from tests.helpers import create_allocation, create_campaign, do_reserve, do_settle, get_budget_field
from topup_worker.main import run_topup_cycle


def test_simulate_second_price_clears_strictly_below_our_bid() -> None:
    rng = random.Random(42)
    our_price_cpm_micros = 5_000_000  # $5 CPM
    won_at_least_once = False
    for _ in range(200):
        won, clearing = simulate_second_price(rng, our_price_cpm_micros, win_rate=1.0)
        assert won is True
        assert 0 < clearing < our_price_cpm_micros
        won_at_least_once = True
    assert won_at_least_once


def test_simulate_second_price_respects_win_rate() -> None:
    rng = random.Random(7)
    won, clearing = simulate_second_price(rng, 5_000_000, win_rate=0.0)
    assert won is False
    assert clearing == 0


def test_make_bid_request_has_exactly_one_impression() -> None:
    rng = random.Random(3)
    for _ in range(20):
        req = make_bid_request(rng)
        assert len(req.imp) == 1


@pytest.mark.asyncio
async def test_second_price_settlement_is_strictly_below_reserved(pool, registry, cluster_id) -> None:
    bid_cpm_micros = 5_000_000
    per_imp_micros = cpm_to_per_imp_micros(bid_cpm_micros)  # the reserved amount

    campaign_id = await create_campaign(pool, bid_cpm_micros=bid_cpm_micros, total_budget_micros=10_000_000)
    await create_allocation(pool, campaign_id, cluster_id, 10_000_000)
    await run_topup_cycle(pool, registry)
    reserve_result = await do_reserve(registry, campaign_id, per_imp_micros, "second-price-resv")
    assert int(reserve_result[0]) == 1

    rng = random.Random(99)
    won, clearing_cpm_micros = simulate_second_price(rng, bid_cpm_micros, win_rate=1.0)
    assert won is True
    settled_price_micros_per_imp = clearing_cpm_micros // 1000

    result = await do_settle(registry, campaign_id, "second-price-resv", settled_price_micros_per_imp, per_imp_micros)
    ok, reason, settled_micros = result
    assert int(ok) == 1
    assert reason == "OK"
    assert settled_micros < per_imp_micros

    assert await get_budget_field(registry, campaign_id, "spent") == settled_micros
