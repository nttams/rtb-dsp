"""AC-39..AC-45 -- monitoring stack checks against a live deployment.

Prometheus and Grafana are provisioned by deploy/ (owned outside this task's scope), so this
module skips loudly rather than failing the suite when they are not reachable.
"""

from __future__ import annotations

import os

import httpx
import pytest

PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")
GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://localhost:3000")

SERVICE_METRICS_URLS = {
    "engine": os.environ.get("ENGINE_URL", "http://localhost:8080") + "/metrics",
    "win-notifier": os.environ.get("WIN_NOTIFIER_URL", "http://localhost:8010") + "/metrics",
    "campaign-app": os.environ.get("CAMPAIGN_APP_URL", "http://localhost:8000") + "/metrics",
    "topup-worker": os.environ.get("TOPUP_WORKER_URL", "http://localhost:8090") + "/metrics",
}


def _prometheus_reachable() -> bool:
    try:
        r = httpx.get(f"{PROMETHEUS_URL}/-/healthy", timeout=1.5)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(
    not _prometheus_reachable(),
    reason=f"Prometheus not reachable at {PROMETHEUS_URL} -- start the monitoring stack first",
)


def test_prometheus_scrapes_all_targets_up() -> None:
    """AC-41: every configured scrape target is healthy.

    Uses the ACTIVE-TARGETS API rather than an ``up`` query. Prometheus keeps a series alive
    for ~5 minutes after its last sample, so ``up`` still returns ``0`` for a job that has been
    removed from the scrape config — this test failed for exactly that reason after
    ``mock-exchange`` (an ephemeral, profile-gated one-shot job) was correctly dropped from
    prometheus.yml. The targets API reflects the live configuration instead of TSDB residue.
    """
    r = httpx.get(f"{PROMETHEUS_URL}/api/v1/targets", params={"state": "active"}, timeout=5.0)
    assert r.status_code == 200
    targets = r.json()["data"]["activeTargets"]
    assert targets, "no active scrape targets reported by Prometheus"

    unhealthy = [
        {"job": t["labels"].get("job"), "health": t["health"], "error": t.get("lastError", "")}
        for t in targets
        if t["health"] != "up"
    ]
    assert unhealthy == [], f"targets not up: {unhealthy}"

    jobs = sorted(t["labels"].get("job") for t in targets)
    assert jobs == ["campaign-app", "engine", "topup-worker", "win-notifier"], (
        f"unexpected scrape target set: {jobs}"
    )


def test_grafana_datasource_and_dashboard_provisioned() -> None:
    r = httpx.get(f"{GRAFANA_URL}/api/health", timeout=5.0)
    assert r.status_code == 200

    auth = ("admin", os.environ.get("GRAFANA_ADMIN_PASSWORD", "rtbdsp"))
    r = httpx.get(f"{GRAFANA_URL}/api/datasources", auth=auth, timeout=5.0)
    assert r.status_code == 200
    names = [d["type"] for d in r.json()]
    assert "prometheus" in names

    r = httpx.get(f"{GRAFANA_URL}/api/search", auth=auth, params={"query": "rtb"}, timeout=5.0)
    assert r.status_code == 200
    assert any("rtb" in d.get("title", "").lower() for d in r.json())


def test_each_service_metrics_endpoint_responds() -> None:
    for name, url in SERVICE_METRICS_URLS.items():
        try:
            r = httpx.get(url, timeout=2.0)
        except httpx.HTTPError:
            pytest.fail(f"{name} /metrics unreachable at {url}")
        assert r.status_code == 200, f"{name} /metrics returned {r.status_code}"
