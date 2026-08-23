"""Narrow OpenRTB 2.5 BidResponse models — only the fields this system reads or writes.

The reservation identity rides back to the mock exchange on ``bid.ext.reservation_id`` and
``bid.ext.cluster`` (verified working against the Rust engine's ``build_bid_response``). These
models must round-trip that pair without loss.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class BidExt(BaseModel):
    # extra="allow": the ext object may legitimately carry more than the reservation identity;
    # anything beyond reservation_id/cluster is preserved, not dropped, for losslessness.
    model_config = ConfigDict(extra="allow")

    reservation_id: str | None = None
    cluster: str | None = None


class Bid(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    impid: str
    price: float
    crid: str | None = None
    cid: str | None = None
    ext: BidExt | None = None


class SeatBid(BaseModel):
    """OpenRTB SeatBid.

    ``seat`` and ``group`` are modeled even though the demo never sets them: the Rust engine
    serializes ``group`` (it is a non-optional field with default 0 in the OpenRTB spec), and
    ``extra="forbid"`` means an unmodeled field is a hard validation error at runtime, not a
    silently ignored one.

    This was a live integration break: the golden-fixture contract test passed because the
    hand-written fixture omitted ``group``, so both sides "agreed" about a field neither
    modeled. Agreement over the fixtures is not conformance to the wire.
    """

    model_config = ConfigDict(extra="forbid")

    bid: list[Bid] = []
    seat: str | None = None
    group: int = 0


class BidResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    seatbid: list[SeatBid] = []
    cur: str = "USD"
