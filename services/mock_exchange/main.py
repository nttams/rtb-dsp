"""mock_exchange — OpenRTB load generator + second-price auction simulator (AC-29..AC-33).

Invoked as ``uv run mock-exchange --rate 200 --duration 90 --engine http://engine:8080``.
Exactly ONE impression per request -- the engine bids on ``imp[0]`` only (Step 3 limitation).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import random
import time
import uuid

import httpx
from prometheus_client import start_http_server

from rtbdsp_common import metrics
from rtbdsp_common.settings import get_settings
from rtbdsp_common.wire.request import Banner, BidRequest, Device, Format, Geo, Imp, Native, User, Video
from rtbdsp_common.wire.response import BidResponse

logger = logging.getLogger("mock_exchange")

# Distributions deliberately OVERLAP the seeder's but are not identical, so some requests
# match nothing (AC-29 / AC-3).
#
# geo.country is ISO-3166-1-alpha-3 on the wire (OpenRTB 2.6 3.2.19: "Country code using
# ISO-3166-1-alpha-3"), NOT alpha-2. device.language is ISO-639-1-alpha-2 (the bare subtag);
# the BCP-47 tag with a region belongs in device.langb, a separate field.
COUNTRIES = ["USA", "GBR", "DEU", "FRA", "JPN", "BRA", "CAN", "AUS"]
# alpha-2 region subtag for each COUNTRIES entry, used only to build an optional `langb`.
_COUNTRY_ALPHA2 = {"USA": "US", "GBR": "GB", "DEU": "DE", "FRA": "FR", "JPN": "JP", "BRA": "BR", "CAN": "CA", "AUS": "AU"}
LANGUAGES = ["en", "de", "fr", "ja", "pt", "es"]
AD_TYPES = ["banner", "video", "native"]
SIZES = [(300, 250), (728, 90), (320, 50), (160, 600), (970, 250), (300, 600), (336, 280)]

requests_total = metrics.counter("mock_exchange_requests_total", "Bid requests sent")
bids_total = metrics.counter("mock_exchange_bids_total", "Bid responses received")
nobids_total = metrics.counter("mock_exchange_nobids_total", "No-bid responses received")
wins_total = metrics.counter("mock_exchange_wins_total", "Auctions won by our bid")
losses_total = metrics.counter("mock_exchange_losses_total", "Auctions lost by our bid")
silent_drops_total = metrics.counter("mock_exchange_silent_drops_total", "Loss notifications deliberately dropped")
settled_spend_micros_total = metrics.counter(
    "mock_exchange_settled_spend_micros_total", "Total settled spend (micro-dollars)"
)


def make_bid_request(rng: random.Random) -> BidRequest:
    """Exactly one impression, randomized ad type/size/geo/language/bidfloor."""
    country = rng.choice(COUNTRIES)
    language = rng.choice(LANGUAGES)
    ad_type = rng.choice(AD_TYPES)
    width, height = rng.choice(SIZES)
    bidfloor = round(rng.uniform(0.10, 3.00), 4)

    banner = video = native = None
    if ad_type == "banner":
        banner = Banner(format=[Format(w=width, h=height)])
    elif ad_type == "video":
        video = Video(mimes=["video/mp4"], w=width, h=height)
    else:
        native = Native(request="{}", ver="1.2")

    imp = Imp(id="1", banner=banner, video=video, native=native, bidfloor=bidfloor, bidfloorcur="USD")
    # langb is optional per spec; set it some of the time to exercise both code paths.
    langb = f"{language}-{_COUNTRY_ALPHA2[country]}" if rng.random() < 0.3 else None
    device = Device(language=language, langb=langb, geo=Geo(country=country))
    return BidRequest(id=str(uuid.uuid4()), imp=[imp], device=device, user=User(id=str(uuid.uuid4())))


def simulate_second_price(rng: random.Random, our_price_cpm_micros: int, win_rate: float) -> tuple[bool, int]:
    """Simulates a second-price auction against a random competing bid.

    Returns ``(won, clearing_price_cpm_micros)``. When we win, the clearing price is drawn
    STRICTLY below our bid so the effect is observable -- with the original (pre-fix) naming
    the settle clamp made every win settle at exactly the reserved amount, so the auction had
    no measurable effect at all (finding M1).
    """
    if rng.random() >= win_rate:
        return False, 0
    clearing = int(our_price_cpm_micros * rng.uniform(0.5, 0.99))
    clearing = max(clearing, 1)
    return True, clearing


async def _one_request(
    client: httpx.AsyncClient, rng: random.Random, args: argparse.Namespace, win_notifier_url: str, stats: dict
) -> None:
    req = make_bid_request(rng)
    stats["requests"] += 1
    requests_total.inc()

    try:
        resp = await client.post(
            f"{args.engine}/openrtb2/bid", json=req.model_dump(mode="json", exclude_none=True)
        )
    except httpx.HTTPError:
        stats["nobids"] += 1
        nobids_total.inc()
        return

    if resp.status_code == 204:
        # Spec-sanctioned no-bid transport (OpenRTB 2.6 2.1) -- a normal outcome, not an error.
        stats["nobids"] += 1
        nobids_total.inc()
        return
    if resp.status_code == 400:
        # A wire-contract break, not a normal no-bid -- log loudly rather than silently
        # folding it into the no-bid count as if the filters had simply rejected the request.
        logger.warning("engine returned 400 for request id=%s: %s", req.id, resp.text[:200])
        stats["nobids"] += 1
        nobids_total.inc()
        return
    if resp.status_code != 200:
        stats["nobids"] += 1
        nobids_total.inc()
        return

    bid_response = BidResponse.model_validate(resp.json())
    if not bid_response.seatbid or not bid_response.seatbid[0].bid:
        stats["nobids"] += 1
        nobids_total.inc()
        return

    bid = bid_response.seatbid[0].bid[0]
    stats["bids"] += 1
    bids_total.inc()

    if bid.ext is None or bid.ext.reservation_id is None or bid.ext.cluster is None:
        return

    reservation_id = bid.ext.reservation_id
    cluster = bid.ext.cluster
    our_price_cpm_micros = int(round(bid.price * 1_000_000))

    won, clearing_cpm_micros = simulate_second_price(rng, our_price_cpm_micros, args.win_rate)

    if won:
        stats["wins"] += 1
        wins_total.inc()
        # bid.price is a CPM; the reservation and settlement are PER-IMPRESSION (finding M1).
        settled_price_micros_per_imp = clearing_cpm_micros // 1000
        try:
            r = await client.post(
                f"{win_notifier_url}/win",
                json={
                    "bid_id": bid.id,
                    "reservation_id": reservation_id,
                    "settled_price_micros_per_imp": settled_price_micros_per_imp,
                    "cluster": cluster,
                },
            )
            if r.status_code == 200:
                body = r.json()
                if body.get("ok"):
                    settled = int(body.get("settled_micros", 0))
                    stats["settled_spend_micros"] += settled
                    settled_spend_micros_total.inc(settled)
        except httpx.HTTPError:
            pass
    else:
        stats["losses"] += 1
        losses_total.inc()
        if rng.random() * 100 < args.silent_loss_pct:
            # Deliberately drop the notification so the TTL reclaim path (AC-16) is
            # exercised during the demo.
            stats["silent_drops"] += 1
            silent_drops_total.inc()
        else:
            try:
                await client.post(
                    f"{win_notifier_url}/loss",
                    json={"bid_id": bid.id, "reservation_id": reservation_id, "cluster": cluster},
                )
            except httpx.HTTPError:
                pass


async def run(args: argparse.Namespace, win_notifier_url: str) -> dict:
    rng = random.Random(args.seed)
    stats = {
        "requests": 0, "bids": 0, "nobids": 0,
        "wins": 0, "losses": 0, "silent_drops": 0,
        "settled_spend_micros": 0,
    }
    interval = 1.0 / args.rate if args.rate > 0 else 0.0

    async with httpx.AsyncClient(timeout=5.0) as client:
        tasks: list[asyncio.Task] = []
        end_time = time.monotonic() + args.duration
        while time.monotonic() < end_time:
            tasks.append(asyncio.create_task(_one_request(client, rng, args, win_notifier_url, stats)))
            if interval:
                await asyncio.sleep(interval)
        if tasks:
            await asyncio.gather(*tasks)

    return stats


def _print_summary(stats: dict) -> None:
    print("mock-exchange run summary")
    print(f"  requests sent:       {stats['requests']}")
    print(f"  bids received:       {stats['bids']}")
    print(f"  no-bids:             {stats['nobids']}")
    print(f"  wins:                {stats['wins']}")
    print(f"  losses:              {stats['losses']}")
    print(f"  silent drops:        {stats['silent_drops']}")
    print(f"  total settled spend: ${stats['settled_spend_micros'] / 1_000_000:.4f}")


def build_parser() -> argparse.ArgumentParser:
    settings = get_settings()
    p = argparse.ArgumentParser(prog="mock-exchange")
    p.add_argument("--rate", type=int, default=settings.exchange_rate)
    p.add_argument("--duration", type=int, default=settings.exchange_duration)
    p.add_argument("--engine", type=str, default=os.environ.get("ENGINE_URL", "http://localhost:8080"))
    p.add_argument("--win-notifier", dest="win_notifier", type=str, default=settings.win_notifier_url)
    p.add_argument("--win-rate", dest="win_rate", type=float, default=settings.exchange_win_rate)
    p.add_argument("--silent-loss-pct", dest="silent_loss_pct", type=float, default=settings.exchange_silent_loss_pct)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument(
        "--metrics-port", dest="metrics_port", type=int,
        default=int(os.environ.get("MOCK_EXCHANGE_METRICS_PORT", "8020")),
    )
    return p


def cli() -> None:
    args = build_parser().parse_args()
    start_http_server(args.metrics_port, registry=metrics.REGISTRY)
    stats = asyncio.run(run(args, args.win_notifier))
    _print_summary(stats)


if __name__ == "__main__":
    cli()
