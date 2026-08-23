# Prometheus metric contract

The Grafana dashboard (`deploy/grafana/provisioning/dashboards/rtb-overview.json`) queries these
names. Emitters must match them exactly — a renamed metric shows up as an empty panel, not an
error, so this file is the contract.

Prefix is `rtbdsp_` everywhere.

## Engine (Rust, `crates/engine/src/metrics.rs`) — `engine:8080/metrics`

| Metric | Type | Notes |
|---|---|---|
| `rtbdsp_bid_requests_total` | counter | Bid requests received |
| `rtbdsp_bids_returned_total` | counter | Bids returned |
| `rtbdsp_nobid_total{reason}` | counter | `reason` ∈ `no_match`, `below_floor`, `no_budget`, `bad_request` |
| `rtbdsp_nonusd_floor_total` | counter | Impressions refused for a non-USD `bidfloorcur` |
| `rtbdsp_budget_fallback_total` | counter | Auction fell through to a next-best campaign |
| `rtbdsp_bid_latency_seconds` | histogram | Bid-path latency; `_bucket` / `_sum` / `_count` |

## Win notifier (Python) — `win-notifier:8010/metrics`

| Metric | Type | Notes |
|---|---|---|
| `rtbdsp_wins_total` | counter | Settled wins |
| `rtbdsp_losses_total` | counter | Released losses |
| `rtbdsp_spend_micros_total` | counter | Confirmed spend in micro-dollars |
| `rtbdsp_settle_clamped_total` | counter | `settle.lua` clamped the settled price — a non-zero value means a unit error |

## Topup worker (Python) — `topup-worker:8090/metrics`

| Metric | Type | Notes |
|---|---|---|
| `rtbdsp_topup_last_success_timestamp_seconds` | gauge | Unix seconds; the dashboard shows `time() - this` as freshness |
| `rtbdsp_reclaim_last_success_timestamp_seconds` | gauge | Same, for the expiry sweeper |
| `rtbdsp_reclaimed_micros_total` | counter | Budget returned by expiry reclaim |
| `rtbdsp_allocation_clamped_total` | counter | Topup floored `allocated` at `reserved + spent` |

## Campaign app (Python) — `campaign-app:8000/metrics`

| Metric | Type | Notes |
|---|---|---|
| `rtbdsp_budget_exhausted_campaigns` | gauge | Campaigns at zero remaining. Derived from the Redis stats/budget hashes |

## Why business counters come from Redis, not process memory

`bids`, `wins`, `losses`, `nobid_budget`, `expired`, and `clamped` live in the
`c:{cluster}:camp:{id}:stats` hash. The campaign dashboard and the Grafana panels both read
from there, so the two views **cannot disagree** (AC-44), and the numbers survive a service
restart. Process-local metrics — latency, HTTP status counts, scrape health — stay in-process
where per-instance attribution is the point.
