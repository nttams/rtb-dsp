//! Pricing, bid-floor gate, and the budget reserve with bounded fallback.

use budget::{money::cpm_to_per_imp_micros, BudgetClient};
use filters::snapshot::CampaignSnapshot;
use filters::CampaignIndex;
use std::sync::atomic::Ordering;
use uuid::Uuid;

use crate::metrics::Metrics;

/// Why the engine did not bid. Kept distinct so `/metrics` can attribute no-bids — lumping
/// them together is how a wire break gets misdiagnosed as a filter bug.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NoBid {
    NoMatch,
    BelowFloor,
    NoBudget,
}

pub struct Win {
    pub creative_id: i64,
    pub campaign_id: i64,
    pub price_cpm_micros: i64,
    pub reservation_id: String,
    pub bid_id: String,
}

/// Rank eligible creatives and reserve budget for the best one that can afford it.
///
/// Ordering is `(bid_cpm DESC, campaign_id ASC, creative_id ASC)` — fully deterministic, so
/// tests are not flaky and the demo is explainable.
///
/// On `INSUFFICIENT` we fall through to the next-best campaign, bounded to `max_fallback`
/// attempts. Without this the highest-CPM campaign exhausts and the whole demo looks dead
/// (Risk R7).
pub async fn run(
    snapshot: &CampaignSnapshot,
    index: &CampaignIndex,
    ctx: &filters::BidContext,
    bidfloor_cpm_micros: Option<i64>,
    client: &BudgetClient,
    ttl_ms: i64,
    max_fallback: usize,
    metrics: &Metrics,
) -> Result<Win, NoBid> {
    let survivors = index.filter(ctx);
    if survivors.is_empty() {
        return Err(NoBid::NoMatch);
    }

    let mut ranked: Vec<(usize, i64)> = survivors
        .into_iter()
        .map(|ci| {
            let camp_idx = index.owner_of(ci);
            (ci, snapshot.campaigns[camp_idx].bid_cpm_micros)
        })
        .collect();

    ranked.sort_by(|a, b| {
        let (ci_a, cpm_a) = *a;
        let (ci_b, cpm_b) = *b;
        cpm_b
            .cmp(&cpm_a)
            .then_with(|| {
                let ca = snapshot.campaigns[index.owner_of(ci_a)].id;
                let cb = snapshot.campaigns[index.owner_of(ci_b)].id;
                ca.cmp(&cb)
            })
            .then_with(|| snapshot.creatives[ci_a].id.cmp(&snapshot.creatives[ci_b].id))
    });

    let mut saw_affordable = false;
    let mut attempts = 0usize;

    for (creative_idx, cpm_micros) in ranked {
        // Bid floor is a CPM, and so is our price — compare like with like.
        if let Some(floor) = bidfloor_cpm_micros {
            if cpm_micros < floor {
                // Ranked descending, so nothing after this clears the floor either.
                break;
            }
        }
        saw_affordable = true;

        if attempts >= max_fallback {
            break;
        }
        attempts += 1;

        let camp_idx = index.owner_of(creative_idx);
        let campaign_id = snapshot.campaigns[camp_idx].id;
        let per_imp = cpm_to_per_imp_micros(cpm_micros);
        let reservation_id = Uuid::new_v4().to_string();
        let bid_id = Uuid::new_v4().to_string();

        match client
            .reserve(campaign_id, per_imp, &reservation_id, &bid_id, ttl_ms)
            .await
        {
            Ok(o) if o.ok => {
                return Ok(Win {
                    creative_id: snapshot.creatives[creative_idx].id,
                    campaign_id,
                    price_cpm_micros: cpm_micros,
                    reservation_id,
                    bid_id,
                });
            }
            Ok(_) => {
                Metrics::inc(&metrics.budget_fallbacks);
                continue;
            }
            Err(e) => {
                tracing::warn!(campaign_id, error = %e, "reserve failed");
                Metrics::inc(&metrics.budget_fallbacks);
                continue;
            }
        }
    }

    if !saw_affordable {
        Err(NoBid::BelowFloor)
    } else {
        Err(NoBid::NoBudget)
    }
}

/// Convenience for tests and callers that want the reason counter bumped.
pub fn record_nobid(metrics: &Metrics, reason: NoBid) {
    match reason {
        NoBid::NoMatch => metrics.nobid_no_match.fetch_add(1, Ordering::Relaxed),
        NoBid::BelowFloor => metrics.nobid_below_floor.fetch_add(1, Ordering::Relaxed),
        NoBid::NoBudget => metrics.nobid_no_budget.fetch_add(1, Ordering::Relaxed),
    };
}
