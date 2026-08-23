"""AC-30' -- cross-language OpenRTB 2.5 model contract test (finding M15).

Two distinct properties:
  1. Agreement: rust_round_trip(fixture) == python_round_trip(fixture), compared over the
     union of fields present in the FIXTURE, not the union of fields present in either
     model set. A field both sides silently drop therefore FAILS, rather than tautologically
     agreeing.
  2. Losslessness: python_round_trip(fixture) == fixture for every modeled field.

The Rust side's artifact (`tests/fixtures/openrtb/.rust_out/<name>.json`) is produced by
`cargo run -p wire --bin roundtrip`. If it is missing or older than its fixture, the agreement
test skips LOUDLY, naming the missing artifact, rather than silently passing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from rtbdsp_common.wire.request import BidRequest
from rtbdsp_common.wire.response import BidResponse

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "openrtb"
RUST_OUT_DIR = FIXTURES_DIR / ".rust_out"

FIXTURE_NAMES = [
    "banner_format", "banner_wh", "video", "native", "geo_lang_floor", "bid_ext_roundtrip",
    "langb_only",
]

_MISSING = object()


def _project(template: Any, data: Any) -> Any:
    """Recursively extract from `data` only the shape present in `template`.

    A field `template` has but `data` lacks becomes `_MISSING` rather than silently vanishing
    from the comparison, so a dropped field is a mismatch, not a tautological agreement.
    """
    if isinstance(template, dict):
        if not isinstance(data, dict):
            return _MISSING
        return {k: _project(v, data.get(k, _MISSING)) for k, v in template.items()}
    if isinstance(template, list):
        if not isinstance(data, list):
            return _MISSING
        return [_project(tv, data[i] if i < len(data) else _MISSING) for i, tv in enumerate(template)]
    return data


def _python_round_trip(fixture: dict) -> dict:
    if "seatbid" in fixture:
        model = BidResponse.model_validate(fixture)
    else:
        model = BidRequest.model_validate(fixture)
    return model.model_dump(mode="json", exclude_none=True)


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / f"{name}.json").read_text())


def _load_rust_out(name: str) -> dict | None:
    fixture_path = FIXTURES_DIR / f"{name}.json"
    rust_path = RUST_OUT_DIR / f"{name}.json"
    if not rust_path.exists():
        return None
    if rust_path.stat().st_mtime < fixture_path.stat().st_mtime:
        return None
    return json.loads(rust_path.read_text())


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_python_round_trip_is_lossless(name: str) -> None:
    fixture = _load_fixture(name)
    py_out = _python_round_trip(fixture)
    projected = _project(fixture, py_out)
    assert projected == fixture, f"{name}: python round-trip dropped or changed a fixture field"


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_rust_and_python_agree(name: str) -> None:
    fixture = _load_fixture(name)
    rust_out = _load_rust_out(name)
    if rust_out is None:
        pytest.skip(
            f"{name}: missing or stale tests/fixtures/openrtb/.rust_out/{name}.json -- "
            f"run `cargo run -p wire --bin roundtrip` before pytest"
        )
    py_out = _python_round_trip(fixture)

    proj_rust = _project(fixture, rust_out)
    proj_py = _project(fixture, py_out)
    assert proj_rust == proj_py, f"{name}: Rust and Python round-trips disagree on fixture fields"
