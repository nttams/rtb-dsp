"""Shared prometheus_client registry + helpers (AC-40).

Each Python service (win-notifier, campaign app, topup worker, mock exchange) registers its
own metrics against :data:`REGISTRY` and exposes them via :func:`metrics_bytes` on
``GET /metrics``. Business counters (bids, wins, losses, no-bid-budget, expired, clamped) are
**sourced from the same Redis stats hashes** the campaign dashboard reads (AC-44) — they are
not independently counted in Python, so Grafana and the dashboard cannot disagree.
"""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, generate_latest

REGISTRY = CollectorRegistry()


def metrics_bytes() -> bytes:
    return generate_latest(REGISTRY)


def counter(name: str, documentation: str, labelnames: tuple[str, ...] = ()) -> Counter:
    return Counter(name, documentation, labelnames, registry=REGISTRY)


def gauge(name: str, documentation: str, labelnames: tuple[str, ...] = ()) -> Gauge:
    return Gauge(name, documentation, labelnames, registry=REGISTRY)


__all__ = ["REGISTRY", "CONTENT_TYPE_LATEST", "metrics_bytes", "counter", "gauge"]
