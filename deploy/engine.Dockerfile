# Multi-stage build for the Rust bidding engine, using cargo-chef for dependency-layer
# caching (Risk R9 / plan §6 Step 8): the first `docker compose up --build` compiles the
# full dependency tree and takes minutes; every subsequent build, as long as Cargo.toml/
# Cargo.lock are unchanged, reuses the cached dependency layer and only recompiles the
# workspace's own source, which takes seconds.
#
# Build context is the repo root (see deploy/docker-compose.yml: context: ..), because the
# workspace spans crates/wire, crates/filters, crates/budget, crates/configstore, and
# crates/engine, plus lua/ (included via include_str! at compile time).

FROM rust:1-slim-bookworm AS chef
WORKDIR /workspace
RUN apt-get update && apt-get install -y --no-install-recommends pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*
RUN cargo install cargo-chef --locked
FROM chef AS planner
COPY Cargo.toml Cargo.lock ./
COPY crates ./crates
COPY lua ./lua
RUN cargo chef prepare --recipe-path recipe.json

FROM chef AS builder
COPY --from=planner /workspace/recipe.json recipe.json
# This layer is cached as long as the dependency graph (recipe.json) is unchanged, even
# when application source below changes on every edit.
RUN cargo chef cook --release --recipe-path recipe.json
COPY Cargo.toml Cargo.lock ./
COPY crates ./crates
COPY lua ./lua
RUN cargo build --release --bin engine

FROM debian:bookworm-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates wget && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /workspace/target/release/engine /usr/local/bin/engine
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/engine"]

# ---------------------------------------------------------------------------
# `toolchain` — the containerized Rust dev/test environment (`make test`, `make fmt`,
# `make wire-roundtrip`). Nothing above depends on it; it exists so the repo needs no
# native rustup/cargo install on the host.
#
# deploy/docker-compose.yml bind-mounts the repo over /workspace and keeps the build
# output in a named volume via CARGO_TARGET_DIR, NOT in the bind-mounted ./target — the
# host's target/ holds host-arch artifacts, and letting a Linux container share it would
# make both toolchains rebuild each other's objects on every switch.
FROM chef AS toolchain
# rust:1-slim ships the `minimal` rustup profile, which has no rustfmt or clippy — without
# this, `make fmt` fails with "cargo-fmt is not installed for the toolchain".
RUN rustup component add rustfmt clippy
ENV CARGO_TARGET_DIR=/cargo-target
WORKDIR /workspace
ENTRYPOINT []
CMD ["cargo", "test", "--workspace"]
