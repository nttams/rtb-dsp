# rtb-dsp

A runnable, end-to-end demonstration DSP (demand-side platform): a Rust bidding engine
receives OpenRTB bid requests, filters campaigns on location/language/ad-type/ad-size
(semantics ported from [rtbkit](https://github.com/rtbkit/rtbkit)), reserves budget in
Redis atomically via Lua, and returns a bid. A Python mock exchange drives traffic; a
Python win-notification service settles reservations into confirmed spend; a
server-rendered campaign app seeds 100 campaigns and shows a live, auto-refreshing
dashboard where budgets visibly drain to zero and exhausted campaigns stop bidding.
Prometheus + Grafana provide an operator's-eye view of the same run.

This is a legibility-first MVP, not a throughput benchmark (see [Non-goals](#non-goals)).

## Architecture

```
                    ┌──────────────────┐        ┌──────────────────┐
                    │  campaign-app    │        │  mock-exchange    │
                    │  (FastAPI, SSR)  │        │  (load generator)  │
                    │  seed / CRUD /   │        │  POST /openrtb2/  │
                    │  dashboard       │        │  bid, win/loss     │
                    └───────┬──────────┘        └─────────┬─────────┘
                            │  writes                      │ bid request
                            ▼                               ▼
                    ┌──────────────────┐        ┌──────────────────┐
                    │   Postgres        │◄──────┤   engine (Rust)   │
                    │ campaigns/        │ loads  │ filter → price →  │
                    │ creatives/        │ config │ reserve via Lua   │
                    │ budget_allocations│        └─────────┬─────────┘
                    └───────┬──────────┘                  │ reserve/settle/
                            │ allocation                    │ release/reclaim
                            ▼                               ▼
                    ┌──────────────────┐        ┌──────────────────┐
                    │  topup-worker     │───────►│      Redis        │
                    │ PG alloc → Redis  │  Lua   │ budget/stats/resv │
                    │ reclaim expired   │ (only)  │  (lua/*.lua)      │
                    └──────────────────┘        └─────────▲─────────┘
                                                            │ settle/release
                                                  ┌──────────┴─────────┐
                                                  │   win-notifier      │
                                                  │  POST /win, /loss   │
                                                  └──────────────────────┘

              ┌──────────────┐        scrapes every /metrics       ┌──────────┐
              │  Prometheus   │◄─────────────────────────────────────┤ Grafana  │
              └──────────────┘                                      └──────────┘
```

| Component | Language | Role |
|---|---|---|
| `engine` | Rust (axum) | Filters, prices, reserves budget, returns one bid. `crates/wire`, `crates/filters`, `crates/budget`, `crates/configstore`, `crates/engine`. |
| `campaign-app` | Python (FastAPI + Jinja2, server-rendered) | Campaign/creative CRUD, 100-campaign seeder, live auto-refreshing dashboard. |
| `win-notifier` | Python (FastAPI) | Settles reservations into confirmed spend (win) or releases them (loss), via Lua. |
| `topup-worker` | Python | Pushes Postgres allocations into each cluster's Redis on an interval; sweeps expired reservations; reconciles spend. |
| `mock-exchange` | Python | Generates randomized OpenRTB requests, simulates a second-price auction, posts win/loss. |
| Postgres | — | Source of truth for campaigns, creatives, and per-cluster budget allocations. |
| Redis | — | Hot budget store: `allocated`/`reserved`/`spent`/stats, mutated only via `lua/*.lua`. |
| Prometheus + Grafana | — | Scrapes every service's `/metrics`; provisioned `rtb-overview` dashboard. |

All money is integer **micro-dollars** (1e-6 USD) end to end — Postgres `BIGINT`, Redis
`HINCRBY`, Rust `i64`, Python `int` — so overspend assertions are exact, never a
floating-point approximation.

## Demo procedure

The whole stack — Rust engine, four Python services, Postgres, Redis, Prometheus,
Grafana — plus the Rust and Python **toolchains** used to build, test and lint it, live in
the single `deploy/docker-compose.yml`. Nothing is installed natively.

**Host requirements: Docker (with Compose v2), plus `make`, `curl` and `grep`.** No
rustup/cargo, no python/uv, no local Postgres or Redis.

```
make demo
```

This runs `docker compose -f deploy/docker-compose.yml up --build -d`, waits for every
service's health check (including the easy-to-forget topup worker — its `/metrics`
endpoint doubles as its health surface), and prints the URLs below.

- **First run:** the Rust engine image compiles the full dependency tree from scratch.
  Expect **several minutes** (dependent on hardware/network). This is a one-time cost:
  the Dockerfile uses `cargo-chef` to cache the dependency layer, so any later
  `make demo` with unchanged `Cargo.lock` recompiles only the workspace's own source and
  finishes in **seconds**.
- **Subsequent runs:** seconds to tens of seconds, dominated by container startup and
  health-check polling, not compilation.

Once the stack is up:

1. Open the campaign dashboard at **http://localhost:8000** and click **Seed 100
   campaigns**.
2. Start the mock exchange (it is `profile: demo`, so it does not autostart with
   everything else):
   ```
   make exchange
   ```
   (`make exchange` is `docker compose --profile demo run --rm --service-ports
   mock-exchange`; `--service-ports` is what publishes its `/metrics` on
   http://localhost:9091, since `compose run` otherwise ignores a service's `ports:`.)
   Default parameters run **200 requests/sec for 90 seconds** (see
   [demo-scale arithmetic](#demo-scale-arithmetic) for why).
3. Watch the dashboard (auto-refreshing, no manual reload): spend climbs, remaining
   budget falls per campaign, and campaigns that exhaust their budget visibly stop
   bidding and show zero remaining.
4. Watch **Prometheus** at **http://localhost:9090** (targets, `up == 1` for every
   service) and **Grafana** at **http://localhost:3000** — login `admin` /
   `$GRAFANA_ADMIN_PASSWORD` (default: `rtbdsp`, see `.env.example`) — for the
   provisioned **RTB Overview** dashboard: bid funnel, no-bid reasons over time, spend
   rate, budget-exhausted campaign count, and topup-worker freshness.

### Everything is reachable from the host

Every service publishes a port, so you can point a browser, `psql`, `redis-cli` or `curl`
at any part of the stack while it runs. Services still address each other by compose
service name, so changing a host port never breaks intra-stack traffic. Override any of
these in `.env.example`.

| Service | Host URL / DSN | Env var |
| --- | --- | --- |
| campaign-app (dashboard) | http://localhost:8000 | `CAMPAIGN_APP_HOST_PORT` |
| engine | http://localhost:8080 | `ENGINE_HOST_PORT` |
| win-notifier | http://localhost:8010 | `WIN_NOTIFIER_HOST_PORT` |
| topup-worker | http://localhost:8090 | `TOPUP_WORKER_HOST_PORT` |
| mock-exchange (metrics, while running) | http://localhost:9091 | `MOCK_EXCHANGE_HOST_PORT` |
| Prometheus | http://localhost:9090 | `PROMETHEUS_HOST_PORT` |
| Grafana | http://localhost:3000 | `GRAFANA_HOST_PORT` |
| Postgres | `postgres://rtb:rtb@localhost:55433/rtbdsp` | `POSTGRES_HOST_PORT` |
| Redis | `redis://localhost:56379/0` | `REDIS_HOST_PORT` |

Postgres and Redis deliberately avoid 5432/6379 so the demo cannot collide with another
local stack.

### Development and tests, also containerized

The Rust and Python toolchains are `tools`-profile services in the same compose file
(`rust-tools`, `py-tools`, `e2e`). They are profile-gated, so `make demo` never starts
them, and they bind-mount the repo, so a host edit applies on the next run with no image
rebuild.

| Command | What it runs |
| --- | --- |
| `make dev` | Rebuild + restart only the app services, leaving datastore and monitoring state up |
| `make test` | `cargo test --workspace` in `rust-tools`, `pytest` in `py-tools` |
| `make e2e` | `make demo`, then `tests/e2e` + the `assert_no_overspend.py` AC-37 gate, from inside the compose network |
| `make fmt` | `cargo fmt` + `ruff format` |
| `make lua-test` | Lua money-invariant tests against a throwaway Redis container |
| `make logs` / `make ps` / `make down` / `make clean` | Stack lifecycle (`clean` also drops the toolchain build caches) |

`py-tools` mounts the host Docker socket, because `services/tests/conftest.py` starts
throwaway Postgres/Redis instances via testcontainers; `TESTCONTAINERS_HOST_OVERRIDE`
points those at `host.docker.internal`, since the throwaway containers are published on
the *daemon's* host rather than inside `py-tools`.

The Rust dependency tree is compiled once into the named volumes `cargotarget` /
`cargoregistry`, so only the first `make test` is slow. Build output never lands in the
host's `./target`, which holds host-arch artifacts.

`make dev` no longer runs anything natively, so there is exactly one way the services ever
run and the demo path and the inner loop cannot drift apart.

## Demo-scale arithmetic

The exchange defaults to **`--rate 200 --duration 90`**: `200 × 90 = 18,000` requests
over the run. Per-impression cost is `bid_cpm / 1000` (a CPM is a cost per **thousand**
impressions). Each seeded campaign's budget is sized as
`impressions_target × (bid_cpm / 1000)`, with `impressions_target` drawn from
**[20, 75]** and CPM **deliberately correlated** with budget rather than drawn
independently — so a campaign that should exhaust in the run actually can, arithmetically,
inside the 18,000-request window.

**For contrast:** the original default parameters (50 rps, 60s duration, budgets drawn
independently from $1–$50) move only **≈$24 total across 100 campaigns** in a run and can
**never** drain a single campaign's budget to zero — the headline result the demo exists
to show would have been structurally unreachable under those settings, regardless of how
correct the rest of the system is.

## rtbkit filters ported

Full per-filter mapping with citations: [`docs/rtbkit-port-notes.md`](docs/rtbkit-port-notes.md).
Summary:

| Filter | rtbkit source | Deviation |
|---|---|---|
| **Location** | `rtbkit/core/router/filters/static_filters.h:208-224` (regex over `Location::fullLocationString()`, `rtbkit/common/bid_request.cc:427-436`: `country:region:city:postal:dma:metro`) | We hash-lookup on **country only**; the `country:region` composite index was dropped as dead code — no `region` column exists in our schema. |
| **Language** | `rtbkit/core/router/filters/static_filters.h:183-200` (regex over `request.language`) | Hash lookup on a normalized primary subtag, not regex — our `include_languages`/`exclude_languages` are exact-subtag arrays, so nothing expressive is lost. |
| **Ad type** | *(no rtbkit counterpart — rtbkit resolves banner/video via exchange connectors, out of scope)* | New filter, written to spec AC-6, following the same bitmask-index shape as the others. |
| **Ad size** | `rtbkit/core/router/filters/creative_filters.h:26-60` (`CreativeFormatFilter`: union of `imp.banner.format[]` plus the `0x0` wildcard) | Ported directly, no behavioral deviation. |

Two structural deviations apply across all four: our `ConfigSet`/`CreativeSet` collapses
rtbkit's 2-D `CreativeMatrix` (`rtbkit/common/filter.h:230-280`) to a flat 1-D
creative bitset (valid because our creative→campaign relationship is 1:1, unlike
rtbkit's independently-managed per-agent creative sets); and rtbkit's growable
`ConfigSet::defaultValue` (`rtbkit/common/filter.h:116-128,153-160`) is replaced by a
**fixed-width invariant asserted in the constructor** — every set is
`with_capacity(n_campaigns)` and never grown, which is what keeps `FixedBitSet`'s
truncate-on-mismatched-length behavior from ever silently dropping high-index campaigns.
Also note: `CreativeLanguageFilter` (`creative_filters.h:86-115`) is **already** an
exact-string list filter in rtbkit, not regex — only `CreativeLocationFilter`
(`creative_filters.h:118-149`) is regex-based; the real creative-language deviation is our
`en-US` → `en` normalization against rtbkit's exact-string match.

We also only evaluate `imp[0]` of each bid request (rtbkit narrows per-impression,
`rtbkit/common/filter.h:382,407`) — a direct consequence of the single-seatbid-single-bid
requirement, since there is nowhere to place a second bid regardless.

## Filters deliberately NOT ported

Segments, PMP, URL, host, hour-of-week, fold position, exchange name, required IDs, user
partition, and lat/long — none of these have a corresponding column or concept in this
domain model (flat campaign → creative, no user/audience data, single mock exchange, no
dayparting), and all are explicit non-goals in the spec. Full rationale per filter:
[`docs/rtbkit-port-notes.md`](docs/rtbkit-port-notes.md#filters-deliberately-not-ported).

Also **not ported**: rtbkit's C++ **event loop** (no multi-exchange/multi-agent
scheduling problem exists for a single stateless HTTP handler), its **ZeroMQ transport**
(no distributed agent processes to transport messages between), and its
**agent-configuration service** (targeting lives in one Postgres-backed CRUD app that the
engine polls, not a live agent registration stream). Details:
[`docs/rtbkit-port-notes.md`](docs/rtbkit-port-notes.md#why-the-c-event-loop-zeromq-and-agent-configuration-service-were-not-ported).

## OpenRTB models (AC-30′)

The Rust engine uses the public [`iab-specs`](https://github.com/remysaissy/iab-specs)
crate (`openrtb_25` feature) for OpenRTB 2.5 types — satisfying the ticket's "use a public
library" requirement on the side that owns the wire contract. **No maintained public
Python OpenRTB 2.5 library exists**: the only PyPI package, `openrtb` 0.2.0 (released
2017-02-02), implements OpenRTB **2.2** and lacks `imp.banner.format[]` (added in 2.3),
which the ad-size filter (AC-7) requires. Python models are instead **vendored** as a
narrow Pydantic v2 subset (`services/rtbdsp_common/wire/`) covering only the fields this
demo touches, and conformance is proven — not asserted — by golden-fixture contract tests
(`tests/fixtures/openrtb/*.json`) round-tripped through **both** the Rust `iab-specs`
types and the Python models, failing if the two languages disagree on any fixture.

## Honest limits of the two flagship tests

- **The ≥10,000-case filter differential test** (`cargo test -p filters`) proves that the
  bitmask index and a linear-scan oracle agree on a large random input space. It does
  **not** prove conformance to rtbkit: both sides of the differential are written by the
  same author against the same understanding of the four filters, so a shared
  misreading of rtbkit's semantics would pass this test undetected. The real conformance
  evidence is a set of **targeted unit tests** asserting specific documented behaviors:
  empty-include-matches-all, the `0x0` wildcard, empty-`imp.formats` no-op, `en-US`→`en`
  normalization, country exclusion, ad-type matching across multiple impressions, and the
  `ConfigSet` fixed-width invariant.
- **The 500-concurrent-`reserve.lua`-calls test** (`cargo test -p budget --test overspend`)
  proves that Redis executes a single Lua script atomically — it does **not** prove the
  system as a whole cannot overspend. The tests that actually cover the paths where a real
  overspend could occur are five targeted **regression tests**:
  1. **Redis flush** — `FLUSHDB` then one topup cycle must restore `spent` from Postgres
     and leave only the unspent remainder available, not a fresh full budget.
  2. **Negative `reserved`** — every decrement clamps at zero, so a reseed or a buggy
     reclaim cannot drive `reserved` negative and hide an overspend from
     `reserved + spent <= allocated`.
  3. **Zero-amount reserve** — `reserve.lua` explicitly rejects `amount <= 0` rather than
     succeeding against a zero-budget campaign forever.
  4. **Lowered allocation** — `topup.lua` floors `allocated` at `reserved + spent` when a
     legal Postgres budget decrease would otherwise retroactively break the invariant.
  5. **Reserve replay** — a duplicate `reserve` call for the same reservation id is a
     no-op, not a double-reservation.

`scripts/assert_no_overspend.py` is the automated AC-37 gate covering all of the above at
runtime, sampled continuously during `make e2e`.

## Monitoring

- **Prometheus:** http://localhost:9090 — scrapes `engine`, `campaign-app`,
  `win-notifier`, `topup-worker`, and `mock-exchange` `/metrics` endpoints every 5s;
  scrape config is committed at `deploy/prometheus/prometheus.yml`.
- **Grafana:** http://localhost:3000 — login **`admin`** / **`$GRAFANA_ADMIN_PASSWORD`**
  (default `rtbdsp`, set in `.env.example`). The Prometheus datasource and the
  **RTB Overview** dashboard (`uid: rtb-overview`) are auto-provisioned from
  `deploy/grafana/provisioning/` on first boot — no manual setup, no click-through
  wizard.
- Business counters shown on both the Grafana board and the campaign dashboard (bids,
  wins, losses, no-bid-budget, expired, clamped) are read from the **same Redis stats
  hashes** in both places, so the two views cannot disagree (AC-44).
- Neither Prometheus nor Grafana is a startup dependency of any application service:
  both start in seconds with their own health checks, so a monitoring failure can never
  block the demo.

## Non-goals

Throughput/latency benchmarking as a deliverable (latency is *observable* via a
Prometheus histogram; no number gates acceptance); any rtbkit filter beyond the four
above; real exchange integrations; frequency capping, pacing, or bid shading; user/
audience data or identity resolution; creative asset hosting; a SPA/client-side
frontend; auth, multi-tenancy, or RBAC; production deployment/IaC/CI.

## Repository layout

```
crates/          Rust workspace: wire, filters, budget, configstore, engine (binary)
lua/             Shared Lua scripts — single source of truth for Redis budget mutation
migrations/      Postgres schema (0001_init.sql, 0002_seed_cluster.sql)
services/        Python: campaign_app, win_notifier, topup_worker, mock_exchange,
                 rtbdsp_common (shared settings/db/redis/metrics/wire models)
deploy/          docker-compose.yml (the whole stack AND the containerized Rust/Python
                 toolchains), Dockerfiles, Prometheus/Grafana provisioning
scripts/         wait_for_health.sh, assert_no_overspend.py
tests/           Cross-language OpenRTB fixtures + end-to-end demo assertions
docs/            redis-keys.md (key contract), rtbkit-port-notes.md (this doc's detail)
```
