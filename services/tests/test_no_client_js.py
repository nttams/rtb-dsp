"""AC-23: no client-side framework. No `<script>` tag and no `onclick=` attribute anywhere in
campaign_app/templates/.
"""

from __future__ import annotations

from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "campaign_app" / "templates"


def test_no_script_tags() -> None:
    files = list(TEMPLATES_DIR.glob("*.html"))
    assert files, "expected at least one template"
    for f in files:
        content = f.read_text().lower()
        assert "<script" not in content, f"{f} contains a <script> tag"


def test_no_onclick_attribute() -> None:
    for f in TEMPLATES_DIR.glob("*.html"):
        content = f.read_text().lower()
        assert "onclick=" not in content, f"{f} contains an onclick= attribute"
