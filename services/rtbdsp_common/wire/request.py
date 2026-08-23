"""Narrow OpenRTB 2.5 BidRequest models — only the fields this system reads.

Fields not covered here (and there are many, in the full spec) are simply absent from these
models; ``model_dump(exclude_unset=True)`` is used everywhere for round-tripping so a field we
never modeled is invisible rather than silently dropped-and-reintroduced with a wrong default.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Format(BaseModel):
    model_config = ConfigDict(extra="forbid")

    w: int | None = None
    h: int | None = None


class Banner(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: list[Format] | None = None
    w: int | None = None
    h: int | None = None


class Video(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mimes: list[str] | None = None
    w: int | None = None
    h: int | None = None


class Native(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: str | None = None
    ver: str | None = None


class Imp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    banner: Banner | None = None
    video: Video | None = None
    native: Native | None = None
    bidfloor: float = 0.0
    bidfloorcur: str = "USD"


class Geo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # ISO-3166-1-alpha-3 on the wire (OpenRTB 2.6 3.2.19), e.g. "USA", not "US". The Rust
    # engine normalizes both directions via filters::country::to_alpha3.
    country: str | None = None


class Device(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # OpenRTB carries language in two places with different formats (2.6 spec):
    #   `language` -- ISO-639-1-alpha-2 bare subtag ("en")
    #   `langb`    -- IETF BCP 47 full tag ("en-US")
    # The engine reads `language` first and falls back to `langb`.
    language: str | None = None
    langb: str | None = None
    geo: Geo | None = None


class User(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None


class BidRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    imp: list[Imp] = []
    device: Device | None = None
    user: User | None = None
