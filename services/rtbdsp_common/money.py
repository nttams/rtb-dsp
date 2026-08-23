"""Integer micro-dollars (1e-6 USD) end to end. Floating-point money is how overspend
assertions become flaky.

MUST agree exactly with ``crates/budget/src/money.rs``; ``test_money.py`` asserts agreement
on the boundaries 1, 999, 1000, 1001, 2_500_000.
"""

from __future__ import annotations


def cpm_to_per_imp_micros(cpm_micros: int) -> int:
    """CPM -> cost of ONE impression, in micro-dollars.

    Ceiling division on purpose. Truncating would send ``bid_cpm_micros = 999`` (a CPM just
    under $0.001) to 0, and a zero-amount reserve succeeds against a zero-budget campaign
    forever (``reserve.lua`` rejects ``amount <= 0``, so truncation would instead make such a
    campaign silently unbiddable). Ceiling keeps every positive CPM costing at least 1
    micro-dollar.
    """
    if cpm_micros <= 0:
        return 0
    return (cpm_micros + 999) // 1000


def per_imp_to_cpm_micros(per_imp_micros: int) -> int:
    """Inverse, for reporting only. Not used in the bid path."""
    return per_imp_micros * 1000
