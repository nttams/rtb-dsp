//! Lua script loading. Both languages read the SAME `lua/*.lua` files, so the money invariant
//! cannot drift between Rust and Python.
//!
//! `_common.lua` defines `now_ms`, `hclamp0`, `num`, `resv_member`, and `parse_member`, and is
//! PREPENDED to every script — Redis has no `require`.

use redis::Script;
use std::sync::OnceLock;

const COMMON: &str = include_str!("../../../lua/_common.lua");
const RESERVE: &str = include_str!("../../../lua/reserve.lua");
const SETTLE: &str = include_str!("../../../lua/settle.lua");
const RELEASE: &str = include_str!("../../../lua/release.lua");
const RECLAIM: &str = include_str!("../../../lua/reclaim_expired.lua");
const TOPUP: &str = include_str!("../../../lua/topup.lua");
const REAP: &str = include_str!("../../../lua/reap_deleted.lua");
const DASHBOARD: &str = include_str!("../../../lua/dashboard_snapshot.lua");

fn compose(body: &str) -> String {
    format!("{COMMON}\n{body}")
}

macro_rules! script {
    ($name:ident, $src:ident) => {
        pub fn $name() -> &'static Script {
            static S: OnceLock<Script> = OnceLock::new();
            S.get_or_init(|| Script::new(&compose($src)))
        }
    };
}

script!(reserve, RESERVE);
script!(settle, SETTLE);
script!(release, RELEASE);
script!(reclaim_expired, RECLAIM);
script!(topup, TOPUP);
script!(reap_deleted, REAP);
script!(dashboard_snapshot, DASHBOARD);
