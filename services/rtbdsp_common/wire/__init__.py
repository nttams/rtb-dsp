"""Vendored, narrow OpenRTB 2.5 Pydantic v2 models (Decision 2b / AC-30').

No maintained public Python OpenRTB 2.5 library exists — PyPI `openrtb` 0.2.0 (2017) models
2.2 and lacks `imp.banner.format[]`, which AC-7 needs. This module only models the fields this
system actually reads or writes; it is not a general-purpose OpenRTB library.
"""

from .request import BidRequest, Device, Geo, Imp, Native, User, Video
from .request import Banner, Format
from .response import Bid, BidExt, BidResponse, SeatBid

__all__ = [
    "BidRequest",
    "Imp",
    "Banner",
    "Format",
    "Video",
    "Native",
    "Device",
    "Geo",
    "User",
    "BidResponse",
    "SeatBid",
    "Bid",
    "BidExt",
]
