//! RTB bid engine. OpenRTB 2.5 in, one seatbid out.

use anyhow::{Context, Result};
use arc_swap::ArcSwap;
use axum::{routing::get, routing::post, Router};
use budget::BudgetClient;
use engine::metrics::Metrics;
use engine::routes;
use engine::state::{AppState, Config, IndexBundle};
use sqlx::postgres::PgPoolOptions;
use std::sync::Arc;
use std::time::Duration;

fn env_or<T: std::str::FromStr>(key: &str, default: T) -> T {
    std::env::var(key).ok().and_then(|v| v.parse().ok()).unwrap_or(default)
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info".into()),
        )
        .init();

    let database_url = std::env::var("DATABASE_URL").context("DATABASE_URL must be set")?;
    let redis_url = std::env::var("REDIS_URL").context("REDIS_URL must be set")?;
    let bind = std::env::var("ENGINE_BIND").unwrap_or_else(|_| "0.0.0.0:8080".into());

    let cfg = Config {
        cluster_id: env_or("CLUSTER_ID", 1),
        cluster_name: std::env::var("CLUSTER_NAME").unwrap_or_else(|_| "us-east".into()),
        reservation_ttl_ms: env_or("RESERVATION_TTL_MS", 5_000),
        max_budget_fallback: env_or("MAX_BUDGET_FALLBACK", 5),
        config_refresh_secs: env_or("CONFIG_REFRESH_SECS", 5),
    };

    let pool = PgPoolOptions::new()
        .max_connections(16)
        .connect(&database_url)
        .await
        .context("connect to Postgres")?;

    let redis = redis::Client::open(redis_url).context("open Redis")?;
    let conn = redis
        .get_multiplexed_async_connection()
        .await
        .context("connect to Redis")?;
    let budget = BudgetClient::new(conn, cfg.cluster_name.clone());

    // Built at startup against tables the `migrate` init service has already applied — the
    // compose graph enforces that ordering, which is why `make demo` has no post-hoc migrate.
    let snapshot = configstore::load_snapshot(&pool, cfg.cluster_id)
        .await
        .context("load initial campaign snapshot")?;
    tracing::info!(
        campaigns = snapshot.campaigns.len(),
        creatives = snapshot.creatives.len(),
        "initial snapshot loaded"
    );

    let refresh_secs = cfg.config_refresh_secs;
    let state = Arc::new(AppState {
        index: ArcSwap::from_pointee(IndexBundle::build(snapshot)),
        pool: pool.clone(),
        budget,
        cfg,
        metrics: Arc::new(Metrics::default()),
    });

    // Rebuild off the request path, then swap atomically (Risk R10).
    {
        let st = state.clone();
        tokio::spawn(async move {
            let mut tick = tokio::time::interval(Duration::from_secs(refresh_secs));
            tick.tick().await; // consume the immediate first tick
            loop {
                tick.tick().await;
                match configstore::load_snapshot(&st.pool, st.cfg.cluster_id).await {
                    Ok(s) => st.index.store(Arc::new(IndexBundle::build(s))),
                    Err(e) => tracing::warn!(error = %e, "snapshot refresh failed; keeping previous index"),
                }
            }
        });
    }

    let app = Router::new()
        .route("/openrtb2/bid", post(routes::bid))
        .route("/healthz", get(routes::healthz))
        .route("/metrics", get(routes::metrics))
        .with_state(state);

    let listener = tokio::net::TcpListener::bind(&bind).await.context("bind")?;
    tracing::info!(%bind, "engine listening");
    axum::serve(listener, app).await.context("serve")?;
    Ok(())
}
