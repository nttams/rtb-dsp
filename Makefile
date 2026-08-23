.PHONY: demo dev up test test-rust test-py migrate seed exchange fmt lint-budget \
        lua-test wire-roundtrip e2e logs ps down clean

# Everything in this repo runs inside ONE compose stack. The only things needed on the
# host are Docker (with Compose v2) and the POSIX basics make/curl/grep — no rustup, no
# cargo, no python, no uv, no local Postgres or Redis. The Rust and Python toolchains
# live in the `tools`-profile services at the bottom of deploy/docker-compose.yml.
COMPOSE := docker compose -f deploy/docker-compose.yml

# --no-deps: the toolchain containers compile and lint source, they do not talk to the
# stack, so they must not drag Postgres/Redis up as a side effect of `make test`.
TOOLS := $(COMPOSE) --profile tools run --rm --no-deps

DASHBOARD_URL := http://localhost:8000
PROMETHEUS_URL := http://localhost:9090
GRAFANA_URL := http://localhost:3000

# The one-command AC-34 path. No post-hoc `make migrate` step here: migrations run inside
# the `migrate` init service before any dependent service starts (finding M19) — the
# engine builds its campaign index from Postgres at startup, so running migrations after
# the health wait would deadlock and this target could never succeed.
demo:
	@echo "First build compiles the full Rust dependency tree and can take several"
	@echo "minutes; cargo-chef caches that layer so subsequent builds take seconds."
	$(COMPOSE) up --build -d
	./scripts/wait_for_health.sh
	@echo ""
	@echo "Demo stack is up. Every service is published on the host:"
	@echo "  Campaign dashboard : $(DASHBOARD_URL)"
	@echo "  Bidding engine     : http://localhost:$${ENGINE_HOST_PORT:-8080}"
	@echo "  Win notifier       : http://localhost:$${WIN_NOTIFIER_HOST_PORT:-8010}"
	@echo "  Topup worker       : http://localhost:$${TOPUP_WORKER_HOST_PORT:-8090}"
	@echo "  Prometheus         : $(PROMETHEUS_URL)"
	@echo "  Grafana            : $(GRAFANA_URL)  (admin / \$$GRAFANA_ADMIN_PASSWORD, default: rtbdsp)"
	@echo "  Postgres           : postgres://rtb:rtb@localhost:$${POSTGRES_HOST_PORT:-55433}/rtbdsp"
	@echo "  Redis              : redis://localhost:$${REDIS_HOST_PORT:-56379}/0"
	@echo ""
	@echo "Seed campaigns from the dashboard, then run the mock exchange:"
	@echo "  make exchange"

# Inner loop: rebuild and restart only the application services, leaving Postgres, Redis
# and the monitoring pair (and therefore all their state) up. This used to run the app
# processes natively on the host against compose infra; it no longer does, so there is
# only one way the services ever run and the two paths cannot drift apart.
dev:
	$(COMPOSE) up --build -d engine campaign-app win-notifier topup-worker
	./scripts/wait_for_health.sh

# Start the stack without rebuilding images.
up:
	$(COMPOSE) up -d
	./scripts/wait_for_health.sh

test: test-rust test-py

test-rust:
	$(TOOLS) rust-tools cargo test --workspace

# services/tests/conftest.py starts throwaway Postgres/Redis via testcontainers; the
# py-tools service mounts the Docker socket so it can do that from inside the container.
test-py:
	$(TOOLS) py-tools pytest tests

migrate:
	$(COMPOSE) run --rm migrate

seed:
	curl -fsS -X POST $(DASHBOARD_URL)/seed

# --service-ports is load-bearing: `docker compose run` ignores a service's `ports:` unless
# asked, so without it the mock exchange's /metrics would be the one thing in the stack not
# reachable from the host.
exchange:
	$(COMPOSE) --profile demo run --rm --service-ports mock-exchange

fmt:
	$(TOOLS) rust-tools cargo fmt --all
	$(TOOLS) py-tools ruff format .

# AC-13 gate: all Redis budget mutation must happen inside lua/*.lua. This greps
# crates/ and services/ (never lua/) for HSET/HINCRBY on a budget-shaped key.
lint-budget:
	@grep -rnE "H(SET|INCRBY)" --include="*.rs" --include="*.py" crates services 2>/dev/null | grep -i "budget" \
		&& (echo "lint-budget: FAIL — budget key mutated outside lua/*.lua"; exit 1) \
		|| echo "lint-budget: OK — no HSET/HINCRBY on a budget key outside lua/*.lua"

# Money-invariant regression tests for lua/*.lua. Starts and tears down its own throwaway
# Redis container; needs no toolchain on the host.
lua-test:
	bash tests/lua/test_money_invariants.sh

wire-roundtrip:
	$(TOOLS) rust-tools cargo test -p wire --test roundtrip
	$(TOOLS) py-tools pytest tests/test_wire_contract.py

# `make e2e` exercises the AC-34 path itself (finding M24 / R16), rather than relying on a
# checklist to keep `make demo` and the inner loop from drifting apart. The e2e service
# runs inside the compose network and includes the assert_no_overspend.py AC-37 gate.
e2e: demo
	$(COMPOSE) --profile tools run --rm e2e

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

down:
	$(COMPOSE) down

# Also drops the toolchain caches (cargotarget/cargoregistry), so the next `make test`
# recompiles the dependency tree from scratch.
clean:
	$(COMPOSE) --profile tools --profile demo down -v
