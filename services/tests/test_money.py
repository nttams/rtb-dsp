"""Ceiling division must agree exactly with crates/budget/src/money.rs and the Rust engine's
verified formula: `(cpm_micros + 999) / 1000` for cpm_micros > 0, else 0.
"""

from rtbdsp_common.money import cpm_to_per_imp_micros

BOUNDARIES = [
    (0, 0),
    (1, 1),
    (999, 1),
    (1000, 1),
    (1001, 2),
    (2_500_000, 2_500),
    (8_000_000, 8_000),
]


def test_ceiling_division_boundaries() -> None:
    for cpm_micros, expected in BOUNDARIES:
        assert cpm_to_per_imp_micros(cpm_micros) == expected, cpm_micros


def test_negative_and_zero_are_zero() -> None:
    assert cpm_to_per_imp_micros(0) == 0
    assert cpm_to_per_imp_micros(-1) == 0
