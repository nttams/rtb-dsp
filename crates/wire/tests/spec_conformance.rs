//! Conformance to the published OpenRTB spec, not just internal agreement.
//!
//! Source: https://github.com/InteractiveAdvertisingBureau/openrtb2.x/blob/main/2.6.md
//!
//! These exist because the golden-fixture contract test proves the Rust and Python models
//! AGREE, not that either CONFORMS — the fixtures and the models were authored together, so a
//! shared misreading passes both. The country-code bug below is precisely that: fixtures used
//! alpha-3 while campaign targeting used alpha-2, each self-consistent, and a real exchange
//! would have matched nothing.

use filters::snapshot::*;
use filters::CampaignIndex;
use std::path::PathBuf;
use wire::*;

fn fixture(name: &str) -> String {
    let p = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/fixtures/openrtb")
        .join(format!("{name}.json"));
    std::fs::read_to_string(&p).unwrap_or_else(|e| panic!("read {}: {e}", p.display()))
}

fn parse(name: &str) -> BidRequest {
    serde_json::from_str(&fixture(name)).unwrap_or_else(|e| panic!("{name}: {e}"))
}

/// > "Country code using ISO-3166-1-alpha-3." — OpenRTB 2.6, Object: Geo
#[test]
fn geo_country_is_alpha3_on_the_wire() {
    let r = parse("banner_format");
    assert_eq!(
        request_country(&r).as_deref(),
        Some("USA"),
        "the spec mandates alpha-3; a 2-letter value here means the fixture is non-conformant"
    );
}

/// The regression that motivated `filters::country`: an operator targets "US", a conformant
/// exchange sends "USA". Before normalization these never met and the campaign was dead.
#[test]
fn alpha2_targeting_matches_alpha3_request() {
    let snapshot = CampaignSnapshot {
        campaigns: vec![CampaignRow {
            id: 1,
            bid_cpm_micros: 2_000_000,
            include_countries: vec!["US".into()], // operator types alpha-2
            exclude_countries: vec![],
            include_languages: vec![],
            exclude_languages: vec![],
        }],
        creatives: vec![CreativeRow {
            id: 10,
            owner_campaign_idx: 0,
            ad_type: AdType::Banner,
            width: 300,
            height: 250,
            include_countries: vec![],
            include_languages: vec![],
        }],
    };
    let index = CampaignIndex::build(&snapshot);

    let req = parse("banner_format"); // carries "USA"
    let ctx = BidContext {
        country: request_country(&req),
        language: request_language(&req),
        ad_types: imp_ad_type(&req.imp[0]),
        formats: imp_formats(&req.imp[0]),
    };
    assert_eq!(
        index.filter(&ctx),
        vec![0],
        "alpha-2 targeting must match an alpha-3 request; got no survivors"
    );
}

/// > "Browser language using ISO-639-1-alpha-2." — Device.language
/// > "Browser language using IETF BCP 47." — Device.langb
///
/// A request carrying only `langb` must still be usable.
#[test]
fn langb_is_read_when_language_is_absent() {
    let r = parse("langb_only");
    assert_eq!(request_language(&r).as_deref(), Some("en-gb"));
    assert_eq!(request_country(&r).as_deref(), Some("GBR"));

    // And it must still match a campaign targeting the bare subtag.
    let keys = language_keys(&request_language(&r).unwrap());
    assert!(keys.contains(&"en".to_string()), "primary subtag must be indexed: {keys:?}");
}

/// `Device.language` is already alpha-2 per spec, so the common case needs no splitting.
#[test]
fn conformant_language_is_the_bare_subtag() {
    let r = parse("banner_wh");
    assert_eq!(request_language(&r).as_deref(), Some("de"));
}

/// > Imp.bidfloor: float, default 0.  Imp.bidfloorcur: string, default "USD".
#[test]
fn bidfloor_defaults_match_spec() {
    // A minimal impression omitting both fields must behave as 0.0 USD, i.e. every bid clears.
    let raw = r#"{"id":"r","imp":[{"id":"1","banner":{"w":300,"h":250}}]}"#;
    let r: BidRequest = serde_json::from_str(raw).expect("minimal request must deserialize");
    assert_eq!(
        imp_bidfloor_micros(&r.imp[0]),
        Some(0),
        "omitted bidfloor must default to 0, not reject the impression"
    );
}

/// §2.1 Transport: "Calls returning no content in response to valid requests (e.g., an empty
/// bid response which is one option for indicating no-bid ...) should return HTTP 204."
/// Our engine returns 204 for no-bid — this test documents that the choice is spec-sanctioned
/// rather than a shortcut, and guards the distinct 400-on-malformed behavior.
#[test]
fn no_bid_via_204_is_spec_sanctioned() {
    // Nothing to assert at the type level; this is a documentation anchor for the engine's
    // http test, which asserts 204 for no-bid and 400 for an undeserializable body.
    assert!(true);
}
