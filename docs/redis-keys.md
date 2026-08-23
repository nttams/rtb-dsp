# Redis key contract (single source of truth)

Both `crates/budget/src/keys.rs` (Rust) and `services/rtbdsp_common/keys.py` (Python) implement
exactly these formats. A contract test asserts the two agree.

| Key | Type | Fields / members |
|---|---|---|
| `c:{cluster}:camp:{campaign_id}:budget` | HASH | `allocated`, `reserved`, `spent`, `allocation_clamped` |
| `c:{cluster}:camp:{campaign_id}:stats`  | HASH | `bids`, `wins`, `losses`, `nobid_budget`, `expired`, `clamped` |
| `c:{cluster}:resv:{reservation_id}`     | HASH | `campaign_id`, `amount`, `bid_id`, `state`, `expires_at_ms`, `settled` |
| `c:{cluster}:resv_index`                | ZSET | member `{reservation_id}:{campaign_id}:{amount}`, score `expires_at_ms` |
| `c:{cluster}:campaigns`                 | SET  | campaign ids present in this cluster's Redis |

`{cluster}` is the cluster **name** (e.g. `us-east`), not its id.

**`state` ∈ `reserved | settled | released | expired`.**

**Derived, never stored:** `available = max(0, allocated - reserved - spent)`.

**The ZSET member is self-describing on purpose** (finding M3). `reclaim_expired.lua` parses
`campaign_id` and `amount` out of the member, never out of the reservation hash, so losing the
hash cannot strand budget no matter how long the sweeper is down.

**Money is integer micro-dollars (1e-6 USD) everywhere.** CPM → per-impression uses **ceiling**
division so any positive CPM costs at least 1 micro-dollar; `reserve.lua` rejects `amount <= 0`
(finding M6).

## Wire-format conformance

Targeting values stored in Postgres and indexed here follow the OpenRTB wire formats, verified
against <https://github.com/InteractiveAdvertisingBureau/openrtb2.x/blob/main/2.6.md>:

| Field | Spec text | Canonical form |
|---|---|---|
| `Geo.country` | "Country code using ISO-3166-1-alpha-3." | `USA`, `GBR`, `DEU` |
| `Device.language` | "Browser language using ISO-639-1-alpha-2." | `en`, `de` |
| `Device.langb` | "Browser language using IETF BCP 47." | `en-US` |

`filters::country::to_alpha3` normalizes alpha-2 targeting input to alpha-3 so an operator may
type `US`. **This mattered:** before it, the schema stored alpha-2 while a conformant exchange
sends alpha-3, so every geo-targeted campaign would have matched nothing — and the golden-fixture
contract test could not catch it, because the fixtures and the seeder were written from the same
misreading. Agreement is not conformance.
