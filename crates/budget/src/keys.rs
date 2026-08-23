//! Redis key builders. The ONLY place key formats are written in Rust.
//! Mirror of `docs/redis-keys.md`; `services/rtbdsp_common/keys.py` is the Python twin.

pub fn budget(cluster: &str, campaign_id: i64) -> String {
    format!("c:{cluster}:camp:{campaign_id}:budget")
}

pub fn stats(cluster: &str, campaign_id: i64) -> String {
    format!("c:{cluster}:camp:{campaign_id}:stats")
}

pub fn reservation(cluster: &str, reservation_id: &str) -> String {
    format!("c:{cluster}:resv:{reservation_id}")
}

pub fn resv_index(cluster: &str) -> String {
    format!("c:{cluster}:resv_index")
}

pub fn campaigns(cluster: &str) -> String {
    format!("c:{cluster}:campaigns")
}

/// The `resv_index` member is self-describing so `reclaim_expired.lua` never needs the
/// reservation hash — that is what makes sweeper downtime harmless (finding M3).
pub fn resv_member(reservation_id: &str, campaign_id: i64, amount_micros: i64) -> String {
    format!("{reservation_id}:{campaign_id}:{amount_micros}")
}
