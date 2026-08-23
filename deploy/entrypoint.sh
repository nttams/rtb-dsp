#!/bin/sh
# Selects which of the four Python services to run inside the shared python.Dockerfile
# image. Invoked as `command: ["<service>", ...extra args]` in deploy/docker-compose.yml.
set -e

service="$1"
shift || true

case "$service" in
  campaign_app)
    exec uvicorn campaign_app.main:app --host "${CAMPAIGN_APP_HOST:-0.0.0.0}" --port "${CAMPAIGN_APP_PORT:-8000}"
    ;;
  win_notifier)
    exec uvicorn win_notifier.main:app --host "${WIN_NOTIFIER_HOST:-0.0.0.0}" --port "${WIN_NOTIFIER_PORT:-8010}"
    ;;
  topup_worker)
    exec python -m topup_worker.main "$@"
    ;;
  mock_exchange)
    exec python -m mock_exchange.main "$@"
    ;;
  *)
    echo "unknown service: '$service' (expected one of: campaign_app win_notifier topup_worker mock_exchange)" >&2
    exit 1
    ;;
esac
