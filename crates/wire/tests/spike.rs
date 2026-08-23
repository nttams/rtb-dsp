//! GATING SPIKE (Risk R1 + finding B2).
//!
//! If this fails on >= 2 of the request fixtures, the decision is to swap `crates/wire` to
//! `openrtb2` 0.3.0 — one file changes. If the `bid.ext` fixture specifically fails, the
//! pre-decided fallback is to overload `bid.id` as `{bid_uuid}|{reservation_id}|{cluster}`.

use filters::snapshot::AdType;
use std::path::PathBuf;
use wire::*;

fn fixture(name: &str) -> String {
    let p = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/fixtures/openrtb")
        .join(format!("{name}.json"));
    std::fs::read_to_string(&p).unwrap_or_else(|e| panic!("read {}: {e}", p.display()))
}

fn parse_req(name: &str) -> BidRequest {
    serde_json::from_str(&fixture(name))
        .unwrap_or_else(|e| panic!("fixture {name} failed to deserialize: {e}"))
}

#[test]
fn banner_with_format_array() {
    let r = parse_req("banner_format");
    assert_eq!(request_country(&r).as_deref(), Some("USA"));
    assert_eq!(request_language(&r).as_deref(), Some("en-us"));
    let imp = &r.imp[0];
    assert_eq!(imp_ad_type(imp), vec![AdType::Banner]);
    assert_eq!(imp_formats(imp), vec![(300, 250), (728, 90)]);
    assert_eq!(imp_bidfloor_micros(imp), Some(500_000));
}

#[test]
fn banner_with_w_h_only_falls_back() {
    let r = parse_req("banner_wh");
    let imp = &r.imp[0];
    assert_eq!(imp_formats(imp), vec![(320, 50)], "must fall back to (w,h) when format[] absent");
    assert_eq!(imp_bidfloor_micros(imp), Some(250_000));
}

#[test]
fn video_impression() {
    let r = parse_req("video");
    let imp = &r.imp[0];
    assert_eq!(imp_ad_type(imp), vec![AdType::Video]);
    // No banner => no formats => the size filter is a no-op (creative_filters.h:48).
    assert!(imp_formats(imp).is_empty());
}

#[test]
fn native_impression() {
    let r = parse_req("native");
    let imp = &r.imp[0];
    assert_eq!(imp_ad_type(imp), vec![AdType::Native]);
    assert_eq!(request_language(&r).as_deref(), Some("pt-br"));
}

#[test]
fn geo_language_and_floor() {
    let r = parse_req("geo_lang_floor");
    assert_eq!(request_country(&r).as_deref(), Some("FRA"));
    assert_eq!(request_language(&r).as_deref(), Some("fr"));
    assert_eq!(imp_bidfloor_micros(&r.imp[0]), Some(3_000_000));
}

/// THE B2 GATE. The reservation identity must survive a serialize -> deserialize round trip,
/// or the entire win/settle path has no key and `settle.lua` can never be called.
#[test]
fn bid_ext_round_trips_reservation_identity() {
    // 1. An externally-authored response deserializes with ext intact.
    let parsed: BidResponse = serde_json::from_str(&fixture("bid_ext_roundtrip"))
        .expect("bid.ext fixture must deserialize");
    let bid = &parsed.seatbid.as_ref().unwrap()[0].bid[0];
    let (rid, cluster) = bid_reservation(bid).expect("ext must carry reservation identity");
    assert_eq!(rid, "resv-abc-123");
    assert_eq!(cluster, "us-east");

    // 2. Our own builder produces the same shape and round-trips.
    let built = build_bid_response(
        "req-1", "1", "bid-1", 2_500_000, 77, 42, "resv-abc-123", "us-east",
    );
    let json = serde_json::to_string(&built).expect("serialize");
    assert!(json.contains("reservation_id"), "ext must survive serialization: {json}");

    let back: BidResponse = serde_json::from_str(&json).expect("deserialize our own output");
    let b = &back.seatbid.as_ref().unwrap()[0].bid[0];
    assert_eq!(bid_reservation(b), Some(("resv-abc-123".into(), "us-east".into())));
    assert_eq!(b.impid, "1");
    assert!((b.price - 2.5).abs() < 1e-9, "price must be CPM 2.5, got {}", b.price);
}

/// Non-USD floors are refused rather than silently mis-bid (documented MVP assumption).
#[test]
fn non_usd_bidfloor_is_refused() {
    let mut r = parse_req("banner_format");
    r.imp[0].bidfloorcur = "EUR".to_string();
    assert_eq!(imp_bidfloor_micros(&r.imp[0]), None);
}
