//! The ONLY crate that imports `iab-specs`. This is the R1 isolation seam: if the crate proves
//! unworkable, swapping to `openrtb2` 0.3.0 changes this file and nothing else.
//!
//! # What the spike actually found (2026-08-22, `iab-specs` 0.5.1)
//!
//! The crate README is wrong in two ways that matter:
//!
//! 1. It shows `Bid::builder::<serde_json::Value>()`. **That does not exist.** `impl Bid` is
//!    written for the default extension only (`v25/bid.rs:174`), so `Bid::builder()` returns a
//!    `BidBuilder<DefaultExt>`. Typed extensions require naming the generic on the type.
//! 2. It shows `version = "0.4"`; crates.io max is `0.5.1`.
//!
//! # Why typed extensions are mandatory, not optional
//!
//! `DefaultExt = Vec<u8>` (opaque bytes). A bid request carrying a JSON `"ext": {...}` object
//! **fails to deserialize** into `Vec<u8>`. So the default is not a safe default for anything
//! that talks to a real exchange, and it is not usable at all for us: the reservation identity
//! has to ride back on `bid.ext`. We therefore fix `Ext = serde_json::Value` everywhere, which
//! satisfies the crate's `Extension` trait through its blanket impl.
//!
//! # Crate quirk worth knowing
//!
//! The `Ext` parameter does not propagate to every child. `Imp.banner` is `Option<Banner>` with
//! the DEFAULT extension, not `Banner<Ext>` (`v25/imp.rs:58`). So `imp.banner.ext` and
//! `imp.banner.format[].ext` must be absent or byte-valued in incoming JSON. We never emit
//! them, and none of the four filters reads them.

use filters::snapshot::AdType;

/// Fixed extension type for every OpenRTB object we touch. See module docs.
pub type Ext = serde_json::Value;

pub type BidRequest = iab_specs::openrtb::v25::BidRequest<Ext>;
pub type BidResponse = iab_specs::openrtb::v25::BidResponse<Ext>;
pub type Imp = iab_specs::openrtb::v25::Imp<Ext>;
pub type Bid = iab_specs::openrtb::v25::Bid<Ext>;
pub type SeatBid = iab_specs::openrtb::v25::SeatBid<Ext>;

/// Micro-dollars per 1e-6 USD.
const MICROS: f64 = 1_000_000.0;

/// `device.geo.country`, normalized to ISO-3166-1 alpha-3.
///
/// > "Country code using ISO-3166-1-alpha-3." — OpenRTB 2.6 §3.2.19 (Object: Geo)
///
/// Normalization is via `filters::country::to_alpha3`, which is also applied to campaign
/// targeting, so an operator may target "US" and still match a conformant "USA" request.
/// `None` when absent — the filters then keep only campaigns with empty include lists,
/// matching rtbkit.
pub fn request_country(req: &BidRequest) -> Option<String> {
    req.device
        .as_ref()?
        .geo
        .as_ref()?
        .country
        .as_ref()
        .map(|c| filters::country::to_alpha3(c))
        .filter(|c| !c.is_empty())
}

/// The request's language, lowercased.
///
/// OpenRTB carries this in two places and they are NOT the same format:
///
/// > "Browser language using ISO-639-1-alpha-2." — `Device.language`
/// > "Browser language using IETF BCP 47." — `Device.langb`
///
/// So `language` is the bare subtag ("en") and `langb` is the full tag ("en-US"). We prefer
/// `language` and fall back to `langb`; `filters::snapshot::language_keys` indexes both the
/// full tag and its primary subtag, so either form matches a campaign targeting "en".
pub fn request_language(req: &BidRequest) -> Option<String> {
    let device = req.device.as_ref()?;
    device
        .language
        .as_ref()
        .map(|l| l.trim().to_ascii_lowercase())
        .filter(|l| !l.is_empty())
        .or_else(|| {
            device
                .langb
                .as_ref()
                .map(|l| l.trim().to_ascii_lowercase())
                .filter(|l| !l.is_empty())
        })
}

/// Which creative types this impression can accept. More than one means the impression
/// matches any of them (union) — see the ad-type filter.
pub fn imp_ad_type(imp: &Imp) -> Vec<AdType> {
    let mut out = Vec::new();
    if imp.banner.is_some() {
        out.push(AdType::Banner);
    }
    if imp.video.is_some() {
        out.push(AdType::Video);
    }
    if imp.native.is_some() {
        out.push(AdType::Native);
    }
    out
}

/// Sizes the impression accepts: `imp.banner.format[]`, falling back to a single
/// `(imp.banner.w, imp.banner.h)` when `format[]` is absent (AC-7).
///
/// An EMPTY result means the size filter is a no-op (`creative_filters.h:48`) — an impression
/// declaring no format is not filtered on size.
pub fn imp_formats(imp: &Imp) -> Vec<(u32, u32)> {
    let Some(banner) = imp.banner.as_ref() else {
        return Vec::new();
    };
    if let Some(fmts) = banner.format.as_ref() {
        let v: Vec<(u32, u32)> = fmts
            .iter()
            .filter_map(|f| match (f.w, f.h) {
                (Some(w), Some(h)) if w > 0 && h > 0 => Some((w as u32, h as u32)),
                _ => None,
            })
            .collect();
        if !v.is_empty() {
            return v;
        }
    }
    match (banner.w, banner.h) {
        (Some(w), Some(h)) if w > 0 && h > 0 => vec![(w as u32, h as u32)],
        _ => Vec::new(),
    }
}

/// Bid floor in micro-dollars (CPM).
///
/// Returns `None` when `bidfloorcur` is present and is not USD — the engine then no-bids and
/// increments `nonusd_floor`. Documented MVP assumption: we do not do FX.
pub fn imp_bidfloor_micros(imp: &Imp) -> Option<i64> {
    let cur = imp.bidfloorcur.trim();
    if !cur.is_empty() && !cur.eq_ignore_ascii_case("USD") {
        return None;
    }
    Some((imp.bidfloor * MICROS).round() as i64)
}

/// Build the single-seatbid, single-bid response, carrying the reservation identity in
/// `bid.ext` so the win notification can find the reservation to settle.
///
/// This is the B2 fix: without it `POST /win` requires a `reservation_id` that nothing ever
/// put on the wire, and `settle.lua` can never be called.
pub fn build_bid_response(
    request_id: &str,
    imp_id: &str,
    bid_id: &str,
    price_cpm_micros: i64,
    creative_id: i64,
    campaign_id: i64,
    reservation_id: &str,
    cluster: &str,
) -> BidResponse {
    let bid = Bid {
        id: bid_id.to_string(),
        impid: imp_id.to_string(),
        price: price_cpm_micros as f64 / MICROS,
        crid: Some(creative_id.to_string()),
        cid: Some(campaign_id.to_string()),
        ext: Some(Box::new(serde_json::json!({
            "reservation_id": reservation_id,
            "cluster": cluster,
        }))),
        ..Default::default()
    };

    BidResponse {
        id: request_id.to_string(),
        seatbid: Some(vec![SeatBid {
            bid: vec![bid],
            ..Default::default()
        }]),
        cur: "USD".to_string(),
        ..Default::default()
    }
}

/// Read the reservation identity back off a bid — used by the mock exchange.
pub fn bid_reservation(bid: &Bid) -> Option<(String, String)> {
    let ext = bid.ext.as_ref()?;
    let rid = ext.get("reservation_id")?.as_str()?.to_string();
    let cluster = ext.get("cluster")?.as_str()?.to_string();
    Some((rid, cluster))
}
