//! rtbkit filter port: location, language, ad type, ad size.
//!
//! Ports the **semantics and the `ConfigSet` set-intersection model** from rtbkit's C++ router
//! filters, not its template machinery, event loop, or messaging. Sources are cited inline by
//! `file:line` against `/rtbkit`; deviations are recorded in `docs/rtbkit-port-notes.md`.
//!
//! # What the tests do and do not prove
//!
//! The differential property test (`tests/differential.rs`) asserts `naive::filter ==
//! index::filter` over >= 10,000 random cases. That proves the bitmask index **agrees with the
//! linear-scan oracle** — it catches indexing, ordering, and set-algebra bugs, which is most of
//! the risk here.
//!
//! It does **not** prove conformance to rtbkit. Both implementations are written by the same
//! author from the same reading of the C++, so a shared misreading passes at 10,000 cases as
//! easily as at 10 — the inverted-`emptyIncludes` failure mode is exactly such a case. **The
//! targeted unit tests in `tests/targeted.rs` are the conformance evidence; the differential
//! test is a consistency net.** Do not present it as the former.

pub mod config_set;
pub mod country;
pub mod include_exclude;
pub mod index;
pub mod list_filter;
pub mod naive;
pub mod snapshot;

pub use config_set::{ConfigSet, CreativeSet};
pub use index::CampaignIndex;
pub use snapshot::{AdType, BidContext, CampaignRow, CampaignSnapshot, CreativeRow};
