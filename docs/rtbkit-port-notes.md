# rtbkit port notes

This document is the detailed per-filter mapping from rtbkit's C++ filters to
`crates/filters`, with `file:line` citations into the read-only reference repo at
`rtbkit/` (relative paths below are relative to that repo root), plus every recorded
deviation. The README's "rtbkit filters ported" section is the summary; this is the
full accounting for reviewers who want more.

**What was ported:** the *semantics* and the **`ConfigSet` bitmask / set-intersection
idea** — filtering as index lookups intersected across filters, rather than a linear
per-campaign scan. **What was not ported:** the C++ template machinery, `boost::u32regex`,
and any coupling to rtbkit's `AgentConfig` / router / transport layers (see "Deliberately
not ported" at the end of this document).

---

## 1. Location filter

- **rtbkit source:** `rtbkit/core/router/filters/static_filters.h:208-224` (`LocationFilter`),
  built on `IncludeExcludeFilter<RegexFilter<boost::u32regex, UnicodeString>>`.
- **Match key:** rtbkit builds a single string from `Location::fullLocationString()`
  (`rtbkit/common/bid_request.cc:427-436`): `country:region:city:postalCode:dma:metro`,
  joined with `:`, and matches campaign include/exclude lists against it with a
  **regex**.
- **Our port:** a **country-only hash lookup** (`HashMap<country_code, ConfigSet>`), not
  a regex over the composite string.
- **Deviation and why:** two changes, both deliberate:
  1. **Regex → hash lookup.** rtbkit's regex model exists because a single filter needed
     to match partial location strings (e.g. a region-only include). Our domain model has
     a single `country` column with no region/city/postal/dma/metro fields, so there is
     nothing partial to match — an exact hash lookup is the correct-and-simpler
     replacement for the fraction of the regex's power we actually use.
  2. **The `country:region` compound index was dropped as dead code.** No `region` column
     exists in `campaigns`/`creatives` (see `migrations/0001_init.sql`), so any index keyed
     on a country+region composite could never be populated or queried — carrying it
     forward would have been unreachable code, not a real feature.

## 2. Language filter

- **rtbkit source:** `rtbkit/core/router/filters/static_filters.h:183-200`
  (`LanguageFilter`), built on `IncludeExcludeFilter<RegexFilter<boost::regex, std::string>>`,
  matching `request.language.utf8String()`.
- **Our port:** hash lookup on a normalized primary language subtag (`en`, `fr`, …), not a
  regex.
- **Deviation and why:** rtbkit's regex include/exclude lists can express partial or
  wildcard language matches; our campaigns store a flat `include_languages`/
  `exclude_languages` array of exact subtags (`migrations/0001_init.sql`), so a hash
  lookup captures the whole feature surface we use with no expressiveness lost. The
  request's `device.language` (e.g. `en-US`) is normalized to its primary subtag (`en`)
  before lookup — see the creative-language deviation below for why this specific
  normalization is called out separately.

## 3. Ad type filter

- **rtbkit source:** **none.** There is no ad-type filter among rtbkit's static or
  creative filters. rtbkit resolves banner vs. video routing through its
  `ExchangeConnector` implementations (exchange-specific request parsing), not through a
  `ConfigSet` filter — that machinery is out of scope (see spec §Filtering; also listed
  under "Deliberately not ported" below, since the agent-configuration / exchange-connector
  layer it lives in was never in scope).
- **Our filter:** a plain `ConfigSet` index keyed by `ad_type ∈ {banner, video, native}`,
  matching `creatives.ad_type` against the impression's populated `imp.banner` /
  `imp.video` / `imp.native` sub-object. This is new code written to the ticket's explicit
  requirement (spec AC-6), following the same bitmask index shape as the ported filters
  for consistency, not a port of any specific rtbkit type.

## 4. Ad size filter

- **rtbkit source:** `rtbkit/core/router/filters/creative_filters.h:26-60`
  (`CreativeFormatFilter`). For each impression, it unions the `ConfigSet` for the
  wildcard `Format(0,0)` ("0x0 means match anything", `creative_filters.h:47-48`) with the
  `ConfigSet` for every format the impression actually declares in `imp.formats` (the
  OpenRTB `imp.banner.format[]` array).
- **Our port:** the same union-of-formats-plus-wildcard logic, ported directly: a
  creative with `width=0, height=0` matches every impression (the wildcard case,
  `creative_filters.h:47-48`); otherwise a creative's `(width, height)` must appear in
  `imp.banner.format[]` (or match `imp.banner.{w,h}` directly for the no-`format[]` case).
- **Deviation:** none behavior-affecting. The C++ version stores the format-keyed index in
  an `unordered_map` guarded by mutation methods (`addCreative`/`removeCreative`); ours is
  a `FixedBitSet`-backed index rebuilt wholesale on each Postgres refresh (see the
  `ConfigSet` deviation below) rather than mutated incrementally — a direct consequence of
  driving the whole index off a periodic snapshot instead of rtbkit's live
  `AgentConfig` add/remove event stream.

---

## Structural deviations (apply across all four filters)

### `CreativeLanguageFilter` is already a list filter — not regex

`rtbkit/core/router/filters/creative_filters.h:86-115` (`CreativeLanguageFilter`) is built
on `CreativeIncludeExcludeFilter<CreativeListFilter<std::string>>` — an **exact-string list
filter**, not a regex. Only `CreativeLocationFilter`
(`rtbkit/core/router/filters/creative_filters.h:118-149`) is regex-based
(`CreativeRegexFilter<boost::u32regex, UnicodeString>`).

**The real creative-language deviation** is therefore not "regex vs. list" (both sides are
already list-shaped) — it is that our engine normalizes the request's `device.language`
tag from `en-US` to its primary subtag `en` before matching, while rtbkit's
`CreativeListFilter` does an **exact string match** with no normalization: an rtbkit
creative list containing `en-US` would not match a request of plain `en`, or vice versa.
This normalization is applied consistently on both the campaign-level and creative-level
language filters (see filter 2 above) so the two layers cannot disagree with each other,
even though it is a real behavioral difference from rtbkit.

### Flat creative-bitset collapse of `CreativeMatrix`

rtbkit's `CreativeMatrix` (`rtbkit/common/filter.h:230-280`) is a **2-D** structure: a
`ConfigSet` (bitmask of campaigns) *per creative*, because rtbkit's `AgentConfig` allows
several creatives per campaign to be filtered independently and re-combined per
impression (`filter.h:396-401`, `creatives(impId)` intersects the campaign mask with the
per-impression creative mask).

Our filters collapse this to a **flat 1-D bitset over creatives directly** (one bit per
creative, not one `ConfigSet` per creative). This is valid *because* our domain model is
flat: budget and campaign-level targeting live only on `campaigns` (see
`migrations/0001_init.sql`), and a creative belongs to exactly one campaign
(`creatives.campaign_id`), so "campaign C survives" and "creative X of campaign C
survives" collapse into a single bit per creative with no information loss. rtbkit needs
the 2-D form because one `AgentConfig` can independently enable/disable creatives without
touching the campaign-level `ConfigSet`; we have no such independent axis.

### `ConfigSet::defaultValue` replaced by a fixed-width invariant

rtbkit's `ConfigSet` (`rtbkit/common/filter.h:116-128`, `:153-160`) supports **growable**
bitsets: `operator&=` calls `expand(other.size())` and then, for indices beyond the
shorter operand's length, ANDs the *tail* against the other set's `defaultValue`
(a single bit standing in for "every index we haven't allocated yet"); `negate()` flips
both the stored bits and `defaultValue` together so a negated growable set still behaves
correctly as it grows.

**We do not port `defaultValue`.** Instead, `crates/filters` asserts a **fixed-width
invariant in the constructor**: every `ConfigSet`/`CreativeSet` is constructed with
`FixedBitSet::with_capacity(n_campaigns)` (or `n_creatives`) and is never grown afterward
(finding M17). This matters because `FixedBitSet::BitAnd` **truncates to the shorter
operand's length** rather than extending with a default value — so if two sets of
different lengths were ever ANDed in our code, the result would silently drop every
index beyond the shorter set's length, which for us means silently dropping every
high-index campaign from the result with no error. The fixed-width invariant makes that
truncation path unreachable by construction instead of relying on a `defaultValue` bit to
paper over a growable-length mismatch. A dedicated unit test asserts the invariant holds
after every mutation.

### `imp[0]`-only bidding

rtbkit narrows filtering **per impression** — `FilterState::creatives(impId)`
(`rtbkit/common/filter.h:382`) and `narrowCreativesForImp(impId, mask)`
(`rtbkit/common/filter.h:407`) both take an impression index, and a real `BidRequest` can
carry multiple `imp[]` entries scored independently.

**Our engine only considers `imp[0]`.** The spec's single-seatbid-single-bid requirement
(spec §Bid decision: "emit one seatbid with one bid") makes multi-impression scoring moot
for the MVP — there is nowhere to put a second bid even if a second impression were
scored. The mock exchange is written to always emit exactly one impression per request
(plan Step 7) specifically so this limitation is never silently exercised by the demo.

### `redis.call('TIME')` effect-replication caveat

Every Lua script needing a timestamp calls `redis.call('TIME')` (`lua/_common.lua`,
`now_ms()`) rather than accepting a caller-supplied timestamp, so that the engine and the
topup worker — different processes with different clocks — cannot disagree about
expiry (finding M7, R19). This has no rtbkit analogue; it is called out here because it
is unrelated to the filter port but affects `reserve.lua`/`reclaim_expired.lua`
correctness: `TIME` is **non-deterministic**, so any of these scripts require Redis's
default **effect replication** (Redis ≥ 5, the default) rather than command
propagation to a replica. The demo runs a single, unreplicated Redis instance, so this
caveat is currently latent — it becomes load-bearing the moment a replica is added.

### In-script key construction (`reclaim_expired.lua`, `dashboard_snapshot.lua`)

Both scripts build per-campaign key names (`c:{cluster}:camp:{id}:budget`, etc.) **inside
Lua** from a `cluster` argument and campaign ids read from `KEYS`/`SMEMBERS`, rather than
having the caller pass every touched key up front. This is simpler for a single-node
Redis (`docs/redis-keys.md`) but has a **Redis Cluster implication**: Cluster requires
every key a script touches to be **declared in `KEYS[]`** up front (via hash tags) so the
cluster can route the script to the shard owning those keys, and it rejects scripts that
touch keys not in `KEYS[]`. In-script key construction is therefore a standalone-Redis
assumption — see the note in `reclaim_expired.lua` itself — and would need every
per-campaign key added to `KEYS[]` (with matching hash tags) before this project could
run on Redis Cluster.

---

## Filters deliberately NOT ported

The ticket and spec (spec §Filtering, §Non-Goals) scope filtering to exactly four
dimensions: location, language, ad type, ad size. Every other rtbkit filter is out of
scope by explicit instruction, not by oversight:

| Filter | rtbkit source | Why not ported |
|---|---|---|
| Segments | `creative_filters.h:241` (`CreativeSegmentsFilter`), `generic_filters.h` (segment filters) | No user/audience segment data exists in this domain model (spec §Non-Goals: "User/audience data, cookie syncing, identity resolution" is explicitly out of scope). |
| PMP (private marketplace) | `creative_filters.h:282` (`CreativePMPFilter`) | No deal/PMP concept modeled; real exchange integrations are out of scope (spec §Non-Goals). |
| URL | `static_filters.h` (`UrlFilter`) | No page/site URL targeting in the ticket's domain model (flat campaign → creative, spec §Domain model). |
| Host | `static_filters.h` (`HostFilter`) | Same as URL — no site/host targeting dimension exists. |
| Hour-of-week | `generic_filters.h` (`HourOfWeekFilter`) | No dayparting/pacing in scope (spec §Non-Goals: "pacing curves"). |
| Fold position | `generic_filters.h` (`FoldPositionFilter`) | No ad-position metadata modeled; not one of the four required dimensions. |
| Exchange name | `static_filters.h` (`ExchangeNameFilter`), `creative_filters.h:151` (`CreativeExchangeNameFilter`) | Single mock exchange, no multi-exchange targeting need (spec §Non-Goals: "real exchange integrations"). |
| Required IDs | `generic_filters.h` (`RequiredIdsFilter`) | Identity resolution is explicitly out of scope (spec §Non-Goals). |
| User partition | `generic_filters.h` (`UserPartitionFilter`) | No user-hashing/AB-partition concept; depends on identity data that is out of scope. |
| Lat/long | part of rtbkit's `Location` targeting (`bid_request.cc`) alongside country/region | Domain model stores only `country` (see the location-filter deviation above); no lat/long column exists to filter on. |

**Priority ordering.** rtbkit runs cheap, high-rejection filters first
(`rtbkit/core/router/filters/priority.h`: `Location` (0x1000) before `Language` (0x1100)
before the creative-level filters (0x2000+)). Our `crates/filters::index` runs the same
relative order — location and language narrow the campaign set before ad type and ad size
narrow the surviving creatives — for the same reason: reject as much as possible as early
as possible.

## Why the C++ event loop, ZeroMQ, and agent-configuration service were not ported

- **The router event loop** (`rtbkit/core/router/`) exists to run rtbkit's own
  multi-exchange, multi-agent auction scheduling and timeout logic. Our engine is a single
  stateless HTTP handler (`axum`) per request — there is no scheduling problem to solve,
  because there is exactly one caller (the mock exchange) and one bid decision per
  request (spec §Filtering, "no custom event loop; use an async HTTP framework").
- **ZeroMQ / `soa/`** is rtbkit's transport layer for its distributed router ↔ agent ↔
  post-auction-loop topology. Our topology is a single Rust binary receiving HTTP and
  reading Postgres/Redis directly — there are no independent agent processes to
  transport messages between.
- **The agent-configuration service** (`AgentConfig`, the live add/remove event stream
  `CreativeFormatFilter::addCreative`/`removeCreative` etc. are built around) exists so
  independently-running bidding agents can register and update their targeting live.
  We have one campaign-management surface (the FastAPI CRUD app) writing directly to
  Postgres, and the engine polls that snapshot on an interval (`CONFIG_REFRESH_SECS`) —
  there are no separate agent processes to configure.

All three are explicitly listed as out of scope in spec §Non-Goals ("Porting rtbkit's C++
event loop, ZeroMQ transport, or agent-configuration service").
