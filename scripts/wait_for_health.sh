#!/usr/bin/env bash
# Polls every service's health/metrics endpoint until all are up, or times out.
#
# The topup worker is the easy one to forget (finding M22 / R21): its /healthz doubles as its
# health surface, and its silent death is exactly the failure mode that makes the demo look
# like a filter bug (full budget, zero bids) rather than a dead worker. It is polled here on
# equal footing with everything else.
#
# PORTABILITY: written for bash 3.2, which is what macOS ships. Associative arrays
# (`declare -A`) are bash 4+ and would abort this script on a stock Mac before it polled
# anything — a demo that must run "on a clean checkout with only Docker" cannot assume bash 4.
set -euo pipefail

ENGINE_URL="${ENGINE_URL:-http://localhost:${ENGINE_HOST_PORT:-8080}}"
CAMPAIGN_APP_URL="${CAMPAIGN_APP_URL:-http://localhost:${CAMPAIGN_APP_HOST_PORT:-8000}}"
WIN_NOTIFIER_URL="${WIN_NOTIFIER_URL:-http://localhost:${WIN_NOTIFIER_HOST_PORT:-8010}}"
TOPUP_WORKER_URL="${TOPUP_WORKER_URL:-http://localhost:${TOPUP_WORKER_HOST_PORT:-8090}}"
PROMETHEUS_URL="${PROMETHEUS_URL:-http://localhost:${PROMETHEUS_HOST_PORT:-9090}}"
GRAFANA_URL="${GRAFANA_URL:-http://localhost:${GRAFANA_HOST_PORT:-3000}}"

TIMEOUT_SECS="${WAIT_FOR_HEALTH_TIMEOUT:-180}"
INTERVAL_SECS=2

# Parallel "name|url" entries — bash 3.2 safe.
CHECKS="
engine|${ENGINE_URL}/healthz
campaign-app|${CAMPAIGN_APP_URL}/metrics
win-notifier|${WIN_NOTIFIER_URL}/metrics
topup-worker|${TOPUP_WORKER_URL}/healthz
prometheus|${PROMETHEUS_URL}/-/healthy
grafana|${GRAFANA_URL}/api/health
"

echo "Waiting for services to become healthy (timeout ${TIMEOUT_SECS}s)..."

elapsed=0
while :; do
  pending=""
  for entry in $CHECKS; do
    name="${entry%%|*}"
    url="${entry#*|}"
    if ! curl -fsS --max-time 3 "$url" >/dev/null 2>&1; then
      pending="$pending $name"
    fi
  done

  if [ -z "$pending" ]; then
    echo "All services healthy."
    exit 0
  fi

  if [ "$elapsed" -ge "$TIMEOUT_SECS" ]; then
    echo "TIMEOUT after ${TIMEOUT_SECS}s. Still unhealthy:$pending" >&2
    echo "" >&2
    echo "Recent logs:" >&2
    for name in $pending; do
      echo "--- $name ---" >&2
      docker compose -f "$(dirname "$0")/../deploy/docker-compose.yml" logs --tail=20 "$name" >&2 2>/dev/null || true
    done
    exit 1
  fi

  echo "  pending:$pending (${elapsed}s)"
  sleep "$INTERVAL_SECS"
  elapsed=$((elapsed + INTERVAL_SECS))
done
