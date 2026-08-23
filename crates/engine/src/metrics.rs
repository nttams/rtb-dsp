//! Prometheus text exposition. Hand-rolled: ~9 counters and one histogram do not justify a
//! metrics framework, and this keeps the dependency surface small.
//!
//! Business counters (bids/wins/losses) are NOT kept here — they live in the Redis stats
//! hashes so Grafana and the campaign dashboard read the same numbers and cannot disagree
//! (AC-44). What lives here is per-process: request counts, no-bid reasons, latency.

use std::sync::atomic::{AtomicU64, Ordering};

#[derive(Default)]
pub struct Metrics {
    pub requests: AtomicU64,
    pub bids: AtomicU64,
    pub nobid_no_match: AtomicU64,
    pub nobid_below_floor: AtomicU64,
    pub nobid_no_budget: AtomicU64,
    pub bad_request: AtomicU64,
    pub nonusd_floor: AtomicU64,
    pub budget_fallbacks: AtomicU64,
    /// Cumulative bucket counts for the bid-path latency histogram, in microseconds.
    latency_buckets: [AtomicU64; LATENCY_BOUNDS.len()],
    latency_sum_us: AtomicU64,
    latency_count: AtomicU64,
}

pub const LATENCY_BOUNDS: [u64; 8] = [100, 250, 500, 1_000, 2_500, 5_000, 10_000, 50_000];

impl Metrics {
    pub fn inc(c: &AtomicU64) {
        c.fetch_add(1, Ordering::Relaxed);
    }

    pub fn observe_latency_us(&self, us: u64) {
        for (i, b) in LATENCY_BOUNDS.iter().enumerate() {
            if us <= *b {
                self.latency_buckets[i].fetch_add(1, Ordering::Relaxed);
            }
        }
        self.latency_sum_us.fetch_add(us, Ordering::Relaxed);
        self.latency_count.fetch_add(1, Ordering::Relaxed);
    }

    pub fn render(&self) -> String {
        let g = |c: &AtomicU64| c.load(Ordering::Relaxed);
        let mut s = String::with_capacity(2048);

        s.push_str("# HELP rtbdsp_bid_requests_total Bid requests received.\n");
        s.push_str("# TYPE rtbdsp_bid_requests_total counter\n");
        s.push_str(&format!("rtbdsp_bid_requests_total {}\n", g(&self.requests)));

        s.push_str("# HELP rtbdsp_bids_returned_total Bids returned.\n# TYPE rtbdsp_bids_returned_total counter\n");
        s.push_str(&format!("rtbdsp_bids_returned_total {}\n", g(&self.bids)));

        s.push_str("# HELP rtbdsp_nobid_total No-bids by reason.\n# TYPE rtbdsp_nobid_total counter\n");
        s.push_str(&format!("rtbdsp_nobid_total{{reason=\"no_match\"}} {}\n", g(&self.nobid_no_match)));
        s.push_str(&format!("rtbdsp_nobid_total{{reason=\"below_floor\"}} {}\n", g(&self.nobid_below_floor)));
        s.push_str(&format!("rtbdsp_nobid_total{{reason=\"no_budget\"}} {}\n", g(&self.nobid_no_budget)));
        s.push_str(&format!("rtbdsp_nobid_total{{reason=\"bad_request\"}} {}\n", g(&self.bad_request)));

        s.push_str("# HELP rtbdsp_nonusd_floor_total Impressions refused for a non-USD bid floor.\n");
        s.push_str("# TYPE rtbdsp_nonusd_floor_total counter\n");
        s.push_str(&format!("rtbdsp_nonusd_floor_total {}\n", g(&self.nonusd_floor)));

        s.push_str("# HELP rtbdsp_budget_fallback_total Times the auction fell through to a next-best campaign.\n");
        s.push_str("# TYPE rtbdsp_budget_fallback_total counter\n");
        s.push_str(&format!("rtbdsp_budget_fallback_total {}\n", g(&self.budget_fallbacks)));

        s.push_str("# HELP rtbdsp_bid_latency_seconds Bid-path latency.\n");
        s.push_str("# TYPE rtbdsp_bid_latency_seconds histogram\n");
        for (i, b) in LATENCY_BOUNDS.iter().enumerate() {
            s.push_str(&format!(
                "rtbdsp_bid_latency_seconds_bucket{{le=\"{}\"}} {}\n",
                *b as f64 / 1e6,
                g(&self.latency_buckets[i])
            ));
        }
        s.push_str(&format!(
            "rtbdsp_bid_latency_seconds_bucket{{le=\"+Inf\"}} {}\n",
            g(&self.latency_count)
        ));
        s.push_str(&format!(
            "rtbdsp_bid_latency_seconds_sum {}\n",
            g(&self.latency_sum_us) as f64 / 1e6
        ));
        s.push_str(&format!("rtbdsp_bid_latency_seconds_count {}\n", g(&self.latency_count)));
        s
    }
}
