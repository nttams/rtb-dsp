use arc_swap::ArcSwap;
use budget::BudgetClient;
use filters::snapshot::CampaignSnapshot;
use filters::CampaignIndex;
use sqlx::PgPool;
use std::sync::Arc;

use crate::metrics::Metrics;

/// The filter index plus the snapshot it was built from, published together so a reader can
/// never see an index that disagrees with the campaign rows it indexes.
pub struct IndexBundle {
    pub snapshot: CampaignSnapshot,
    pub index: CampaignIndex,
}

impl IndexBundle {
    pub fn build(snapshot: CampaignSnapshot) -> Self {
        let index = CampaignIndex::build(&snapshot);
        Self { snapshot, index }
    }
}

pub struct Config {
    pub cluster_id: i32,
    pub cluster_name: String,
    pub reservation_ttl_ms: i64,
    pub max_budget_fallback: usize,
    pub config_refresh_secs: u64,
}

pub struct AppState {
    /// Rebuilt off the request path and swapped in atomically, so a refresh never tears a
    /// read and no in-flight request is dropped (Risk R10).
    pub index: ArcSwap<IndexBundle>,
    pub pool: PgPool,
    pub budget: BudgetClient,
    pub cfg: Config,
    pub metrics: Arc<Metrics>,
}
