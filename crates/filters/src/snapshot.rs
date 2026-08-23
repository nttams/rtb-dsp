//! Plain data types describing the campaign/creative population.
//! `configstore` fills these from Postgres; this crate never touches a database
//! and never imports an OpenRTB type.

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum AdType {
    Banner,
    Video,
    Native,
}

#[derive(Debug, Clone)]
pub struct CampaignRow {
    pub id: i64,
    pub bid_cpm_micros: i64,
    pub include_countries: Vec<String>,
    pub exclude_countries: Vec<String>,
    pub include_languages: Vec<String>,
    pub exclude_languages: Vec<String>,
}

#[derive(Debug, Clone)]
pub struct CreativeRow {
    pub id: i64,
    /// Index into `CampaignSnapshot::campaigns`. Our model is flat: a creative belongs to
    /// exactly one campaign, which is what lets us collapse rtbkit's 2-D `CreativeMatrix`.
    pub owner_campaign_idx: usize,
    pub ad_type: AdType,
    /// `0 x 0` is the match-anything wildcard (`creative_filters.h:51`).
    pub width: u32,
    pub height: u32,
    pub include_countries: Vec<String>,
    pub include_languages: Vec<String>,
}

#[derive(Debug, Clone, Default)]
pub struct CampaignSnapshot {
    pub campaigns: Vec<CampaignRow>,
    pub creatives: Vec<CreativeRow>,
}

/// Everything the filters need from a bid request, extracted by `crates/wire` so this
/// crate stays free of OpenRTB types.
///
/// MVP bids on `imp[0]` only, so there is no per-impression dimension here. rtbkit keeps a
/// `CreativeMatrix` per impression (`filter.h:382`, `narrowCreativesForImp` at `:407`).
#[derive(Debug, Clone, Default)]
pub struct BidContext {
    /// Uppercase ISO-3166-1 alpha-2. `None` when the request carries no `device.geo.country`.
    pub country: Option<String>,
    /// Lowercase language tag as sent, e.g. `en-us`.
    pub language: Option<String>,
    /// Which of `imp.banner` / `imp.video` / `imp.native` are present. More than one means
    /// the impression matches any of them (union).
    pub ad_types: Vec<AdType>,
    /// From `imp.banner.format[]`, falling back to a single `(w, h)`. EMPTY means the size
    /// filter is a no-op (`creative_filters.h:48`).
    pub formats: Vec<(u32, u32)>,
}

/// Normalize a language tag: lowercase, and yield both the full tag and its primary subtag
/// so a campaign targeting `en` matches a request declaring `en-US`.
///
/// OpenRTB specifies `Device.language` as **ISO-639-1-alpha-2** ("Browser language using
/// ISO-639-1-alpha-2.") and puts the BCP-47 tag in `Device.langb` ("Browser language using
/// IETF BCP 47."). So a conformant `language` is already the bare subtag; the extra
/// normalization here is what lets us also accept a `langb`-style value such as `en-US`
/// without a second code path.
///
/// Deviation from rtbkit, recorded in `docs/rtbkit-port-notes.md`: rtbkit matches the language
/// by regex at the campaign level (`static_filters.h:195`) and by EXACT STRING at the creative
/// level (`creative_filters.h:110` is a list filter over `request.language.utf8String()`).
/// Our list model normalizes instead.
pub fn language_keys(tag: &str) -> Vec<String> {
    let lower = tag.trim().to_ascii_lowercase();
    if lower.is_empty() {
        return Vec::new();
    }
    let primary: String = lower
        .split(|c| c == '-' || c == '_')
        .next()
        .unwrap_or(&lower)
        .to_string();
    if primary == lower {
        vec![lower]
    } else {
        vec![lower, primary]
    }
}

/// Canonical country key: ISO-3166-1 **alpha-3**, matching OpenRTB's `Geo.country`
/// ("Country code using ISO-3166-1-alpha-3." — OpenRTB 2.6 §3.2.19). Accepts alpha-2 targeting
/// input and normalizes it, so an operator may type either. See `country::to_alpha3`.
pub fn country_key(c: &str) -> String {
    crate::country::to_alpha3(c)
}
