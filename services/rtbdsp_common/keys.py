"""Redis key builders. Mirror of ``docs/redis-keys.md`` and the Rust twin
``crates/budget/src/keys.rs``. A contract test asserts the two agree.
"""

from __future__ import annotations


def budget(cluster: str, campaign_id: int) -> str:
    return f"c:{cluster}:camp:{campaign_id}:budget"


def stats(cluster: str, campaign_id: int) -> str:
    return f"c:{cluster}:camp:{campaign_id}:stats"


def reservation(cluster: str, reservation_id: str) -> str:
    return f"c:{cluster}:resv:{reservation_id}"


def resv_index(cluster: str) -> str:
    return f"c:{cluster}:resv_index"


def campaigns(cluster: str) -> str:
    return f"c:{cluster}:campaigns"


def resv_member(reservation_id: str, campaign_id: int, amount_micros: int) -> str:
    """The ``resv_index`` member is self-describing so ``reclaim_expired.lua`` never needs the
    reservation hash — that is what makes sweeper downtime harmless (finding M3).
    """
    return f"{reservation_id}:{campaign_id}:{amount_micros}"


def parse_member(member: str) -> tuple[str, str, int] | None:
    """Inverse of :func:`resv_member`. Returns ``None`` if unparseable."""
    parts = member.rsplit(":", 2)
    if len(parts) != 3:
        return None
    rid, cid, amt = parts
    try:
        return rid, cid, int(amt)
    except ValueError:
        return None
