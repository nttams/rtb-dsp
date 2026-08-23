# One image for all four Python services (campaign_app, win_notifier, topup_worker,
# mock_exchange) built from the single services/pyproject.toml uv-managed environment.
# The concrete entry point is selected at `docker compose run` time via the container
# `command:` (see deploy/docker-compose.yml), not baked into the image, so one build
# serves every Python service.

FROM python:3.12-slim-bookworm AS base
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /bin/
WORKDIR /workspace

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/workspace/services/.venv

# Dependency layer: cached across source edits as long as pyproject.toml / uv.lock are
# unchanged.
COPY services/pyproject.toml services/uv.lock* ./services/
RUN cd services && (uv sync --frozen --no-install-project || uv sync --no-install-project)

# Application source, plus the shared Lua scripts the Python Redis clients register.
COPY services ./services
COPY lua ./lua
COPY deploy/entrypoint.sh /workspace/entrypoint.sh
RUN chmod +x /workspace/entrypoint.sh

RUN cd services && (uv sync --frozen || uv sync)

ENV PATH="/workspace/services/.venv/bin:${PATH}"
WORKDIR /workspace/services

ENTRYPOINT ["/workspace/entrypoint.sh"]

# ---------------------------------------------------------------------------
# `dev` — the containerized Python dev/test environment (`make test`, `make e2e`,
# `make fmt`). Same lockfile as `base`, plus the `dev` extra (pytest, testcontainers,
# ruff), so the repo needs no native python/uv install on the host.
#
# The virtualenv lives at /opt/venv, NOT at services/.venv: deploy/docker-compose.yml
# bind-mounts the repo over /workspace, and a host-built services/.venv would otherwise
# shadow the image's own and break every import. Source is reached via PYTHONPATH
# (set in compose) rather than an editable install, so edits take effect with no rebuild.
FROM python:3.12-slim-bookworm AS dev
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /bin/
WORKDIR /workspace

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

COPY services/pyproject.toml services/uv.lock* ./services/
RUN cd services && (uv sync --frozen --extra dev --no-install-project \
      || uv sync --extra dev --no-install-project)

ENV PATH="/opt/venv/bin:${PATH}"
WORKDIR /workspace/services
ENTRYPOINT []
CMD ["pytest", "tests"]
