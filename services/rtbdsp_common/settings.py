"""Env-driven configuration shared by all Python services. Mirrors ``.env.example``."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, str(default)))


@dataclass(frozen=True)
class Settings:
    # --- datastores ---
    database_url: str = field(default_factory=lambda: _env(
        "DATABASE_URL", "postgres://rtb:rtb@localhost:5432/rtbdsp"
    ))
    # NOTE: python services resolve per-cluster Redis URLs from the `clusters` table via
    # redis_client.client_for() (finding M20) and do not use REDIS_URL directly, except as
    # a fallback default when no cluster row applies (e.g. local scripts/tests).
    redis_url: str = field(default_factory=lambda: _env("REDIS_URL", "redis://localhost:6379/0"))
    cluster_id: int = field(default_factory=lambda: _env_int("CLUSTER_ID", 1))
    cluster_name: str = field(default_factory=lambda: _env("CLUSTER_NAME", "us-east"))

    # --- engine (read-only reference; the engine itself is Rust) ---
    reservation_ttl_ms: int = field(default_factory=lambda: _env_int("RESERVATION_TTL_MS", 5000))
    max_budget_fallback: int = field(default_factory=lambda: _env_int("MAX_BUDGET_FALLBACK", 5))

    # --- workers ---
    topup_interval_secs: float = field(default_factory=lambda: _env_float("TOPUP_INTERVAL_SECS", 5))
    reclaim_interval_secs: float = field(default_factory=lambda: _env_float("RECLAIM_INTERVAL_SECS", 1))
    reconcile_interval_secs: float = field(default_factory=lambda: _env_float("RECONCILE_INTERVAL_SECS", 30))
    topup_health_port: int = field(default_factory=lambda: _env_int("TOPUP_HEALTH_PORT", 8090))

    # --- services ---
    campaign_app_bind: str = field(default_factory=lambda: _env("CAMPAIGN_APP_BIND", "0.0.0.0:8000"))
    win_notifier_bind: str = field(default_factory=lambda: _env("WIN_NOTIFIER_BIND", "0.0.0.0:8010"))
    win_notifier_url: str = field(default_factory=lambda: _env("WIN_NOTIFIER_URL", "http://localhost:8010"))

    # --- demo scale ---
    seed_impressions_min: int = field(default_factory=lambda: _env_int("SEED_IMPRESSIONS_MIN", 20))
    seed_impressions_max: int = field(default_factory=lambda: _env_int("SEED_IMPRESSIONS_MAX", 75))
    exchange_rate: int = field(default_factory=lambda: _env_int("EXCHANGE_RATE", 200))
    exchange_duration: int = field(default_factory=lambda: _env_int("EXCHANGE_DURATION", 90))
    exchange_win_rate: float = field(default_factory=lambda: _env_float("EXCHANGE_WIN_RATE", 0.40))
    exchange_silent_loss_pct: float = field(default_factory=lambda: _env_float("EXCHANGE_SILENT_LOSS_PCT", 5))

    # --- monitoring ---
    grafana_admin_password: str = field(default_factory=lambda: _env("GRAFANA_ADMIN_PASSWORD", "rtbdsp"))


def get_settings() -> Settings:
    return Settings()


def bind_host_port(bind: str) -> tuple[str, int]:
    """Parse a `HOST:PORT` bind string as used by `.env.example`."""
    host, _, port = bind.rpartition(":")
    return host or "0.0.0.0", int(port)
