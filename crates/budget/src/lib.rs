//! Redis budget client. Every mutation goes through a Lua script — no read-modify-write of
//! Redis budget state from Rust, ever. That is what makes AC-15 / AC-37 provable.

pub mod keys;
pub mod money;
pub mod scripts;

use anyhow::Result;
use redis::aio::MultiplexedConnection;

/// The `{ok, reason, value}` triple every money script returns.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LuaOutcome {
    pub ok: bool,
    pub reason: String,
    pub value: i64,
}

impl From<(i64, String, i64)> for LuaOutcome {
    fn from(t: (i64, String, i64)) -> Self {
        Self { ok: t.0 == 1, reason: t.1, value: t.2 }
    }
}

#[derive(Clone)]
pub struct BudgetClient {
    conn: MultiplexedConnection,
    cluster: String,
}

impl BudgetClient {
    pub fn new(conn: MultiplexedConnection, cluster: impl Into<String>) -> Self {
        Self { conn, cluster: cluster.into() }
    }

    pub fn cluster(&self) -> &str {
        &self.cluster
    }

    /// Atomic check-and-reserve at bid time. Returns `ok = false` with reason
    /// `NO_BUDGET` / `INSUFFICIENT` / `BAD_AMOUNT` when the campaign cannot bid.
    ///
    /// A repeated call with the same `reservation_id` returns `REPLAY` and does NOT
    /// double-increment `reserved` (finding M2).
    pub async fn reserve(
        &self,
        campaign_id: i64,
        amount_micros: i64,
        reservation_id: &str,
        bid_id: &str,
        ttl_ms: i64,
    ) -> Result<LuaOutcome> {
        let c = &self.cluster;
        let out: (i64, String, i64) = scripts::reserve()
            .key(keys::budget(c, campaign_id))
            .key(keys::reservation(c, reservation_id))
            .key(keys::resv_index(c))
            .key(keys::stats(c, campaign_id))
            .arg(amount_micros)
            .arg(reservation_id)
            .arg(bid_id)
            .arg(campaign_id)
            .arg(ttl_ms)
            .invoke_async(&mut self.conn.clone())
            .await?;
        Ok(out.into())
    }

    /// Convert a reservation into confirmed spend.
    ///
    /// `settled_price_micros_per_imp` is PER-IMPRESSION, not CPM — `bid.price` is a CPM, so
    /// the caller must divide by 1000 first (finding M1).
    pub async fn settle(
        &self,
        campaign_id: i64,
        reservation_id: &str,
        settled_price_micros_per_imp: i64,
        amount_micros: i64,
    ) -> Result<LuaOutcome> {
        let c = &self.cluster;
        let out: (i64, String, i64) = scripts::settle()
            .key(keys::reservation(c, reservation_id))
            .key(keys::budget(c, campaign_id))
            .key(keys::resv_index(c))
            .key(keys::stats(c, campaign_id))
            .arg(reservation_id)
            .arg(settled_price_micros_per_imp)
            .arg(campaign_id)
            .arg(amount_micros)
            .invoke_async(&mut self.conn.clone())
            .await?;
        Ok(out.into())
    }

    /// Return a reservation's budget on an explicit loss.
    pub async fn release(&self, campaign_id: i64, reservation_id: &str) -> Result<LuaOutcome> {
        let c = &self.cluster;
        let out: (i64, String, i64) = scripts::release()
            .key(keys::reservation(c, reservation_id))
            .key(keys::budget(c, campaign_id))
            .key(keys::resv_index(c))
            .key(keys::stats(c, campaign_id))
            .arg(reservation_id)
            .arg(campaign_id)
            .invoke_async(&mut self.conn.clone())
            .await?;
        Ok(out.into())
    }
}
