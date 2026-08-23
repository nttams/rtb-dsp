//! Conformance evidence for AC-4..AC-7. These, not the differential test, are what prove the
//! port matches rtbkit — the differential test only proves the index agrees with the oracle.

use filters::snapshot::*;
use filters::{CampaignIndex, CampaignSnapshot};

fn camp(id: i64, inc_c: &[&str], exc_c: &[&str], inc_l: &[&str], exc_l: &[&str]) -> CampaignRow {
    CampaignRow {
        id,
        bid_cpm_micros: 1_000_000,
        include_countries: inc_c.iter().map(|s| s.to_string()).collect(),
        exclude_countries: exc_c.iter().map(|s| s.to_string()).collect(),
        include_languages: inc_l.iter().map(|s| s.to_string()).collect(),
        exclude_languages: exc_l.iter().map(|s| s.to_string()).collect(),
    }
}

fn cre(id: i64, owner: usize, t: AdType, w: u32, h: u32) -> CreativeRow {
    CreativeRow {
        id,
        owner_campaign_idx: owner,
        ad_type: t,
        width: w,
        height: h,
        include_countries: vec![],
        include_languages: vec![],
    }
}

fn ctx(country: Option<&str>, lang: Option<&str>, types: &[AdType], fmts: &[(u32, u32)]) -> BidContext {
    BidContext {
        country: country.map(|s| s.to_string()),
        language: lang.map(|s| s.to_string()),
        ad_types: types.to_vec(),
        formats: fmts.to_vec(),
    }
}

/// (1) generic_filters.h:541,619,630 — an EMPTY include list means match everything.
/// Getting this backwards silently drops every untargeted campaign.
#[test]
fn empty_include_list_matches_anything() {
    let s = CampaignSnapshot {
        campaigns: vec![camp(1, &[], &[], &[], &[])],
        creatives: vec![cre(10, 0, AdType::Banner, 300, 250)],
    };
    let idx = CampaignIndex::build(&s);
    let got = idx.filter(&ctx(Some("JP"), Some("ja"), &[AdType::Banner], &[(300, 250)]));
    assert_eq!(got, vec![0], "untargeted campaign must match an arbitrary request");
}

/// (2) creative_filters.h:51 — Format(0,0) is the match-anything wildcard.
#[test]
fn zero_by_zero_is_a_size_wildcard() {
    let s = CampaignSnapshot {
        campaigns: vec![camp(1, &[], &[], &[], &[])],
        creatives: vec![cre(10, 0, AdType::Banner, 0, 0)],
    };
    let idx = CampaignIndex::build(&s);
    assert_eq!(idx.filter(&ctx(Some("US"), None, &[], &[(728, 90)])), vec![0]);
    assert_eq!(idx.filter(&ctx(Some("US"), None, &[], &[(160, 600)])), vec![0]);
}

/// (3) creative_filters.h:48 — the size filter is a NO-OP when imp.formats is empty.
#[test]
fn empty_imp_formats_is_a_noop() {
    let s = CampaignSnapshot {
        campaigns: vec![camp(1, &[], &[], &[], &[])],
        creatives: vec![cre(10, 0, AdType::Banner, 300, 250)],
    };
    let idx = CampaignIndex::build(&s);
    assert_eq!(
        idx.filter(&ctx(Some("US"), None, &[], &[])),
        vec![0],
        "an impression declaring no format must not be filtered on size"
    );
}

/// (4) en-US request matches an `en` campaign (our normalization deviation).
#[test]
fn language_subtag_normalization() {
    let s = CampaignSnapshot {
        campaigns: vec![camp(1, &[], &[], &["en"], &[])],
        creatives: vec![cre(10, 0, AdType::Banner, 300, 250)],
    };
    let idx = CampaignIndex::build(&s);
    assert_eq!(idx.filter(&ctx(Some("US"), Some("en-US"), &[], &[])), vec![0]);
    assert!(idx.filter(&ctx(Some("US"), Some("fr"), &[], &[])).is_empty());
}

/// (5) exclude_countries containing the request country removes the campaign.
#[test]
fn exclude_country_removes_campaign() {
    let s = CampaignSnapshot {
        campaigns: vec![camp(1, &[], &["CA"], &[], &[])],
        creatives: vec![cre(10, 0, AdType::Banner, 300, 250)],
    };
    let idx = CampaignIndex::build(&s);
    assert!(idx.filter(&ctx(Some("CA"), None, &[], &[])).is_empty());
    assert_eq!(idx.filter(&ctx(Some("US"), None, &[], &[])), vec![0]);
}

/// (6) Ad type — the ONLY one of the four with no rtbkit counterpart, so it is both the most
/// likely to be specified wrong and (before this test) the only one with no independent check.
#[test]
fn ad_type_selection_and_multi_imp_union() {
    let s = CampaignSnapshot {
        campaigns: vec![camp(1, &[], &[], &[], &[])],
        creatives: vec![
            cre(10, 0, AdType::Banner, 0, 0),
            cre(11, 0, AdType::Video, 0, 0),
            cre(12, 0, AdType::Native, 0, 0),
        ],
    };
    let idx = CampaignIndex::build(&s);
    assert_eq!(idx.filter(&ctx(Some("US"), None, &[AdType::Video], &[])), vec![1]);
    // An impression carrying more than one type matches any of them (union).
    let mut both = idx.filter(&ctx(Some("US"), None, &[AdType::Banner, AdType::Native], &[]));
    both.sort();
    assert_eq!(both, vec![0, 2]);
}

/// (7) THE TRUNCATION REGRESSION. rtbkit's ConfigSet::defaultValue (filter.h:125-126, :157)
/// makes a negated short set behave as all-ones in its tail. FixedBitSet's BitAnd truncates to
/// the minimum length instead, which would silently drop every high-index campaign from any
/// request whose country appears in an exclude list.
#[test]
fn sparse_exclude_does_not_truncate_high_index_campaigns() {
    let n = 100;
    let mut campaigns = Vec::new();
    let mut creatives = Vec::new();
    for i in 0..n {
        // Only campaign 3 excludes CA; every other campaign is untargeted.
        let exc: &[&str] = if i == 3 { &["CA"] } else { &[] };
        campaigns.push(camp(i as i64, &[], exc, &[], &[]));
        creatives.push(cre(1000 + i as i64, i, AdType::Banner, 0, 0));
    }
    let s = CampaignSnapshot { campaigns, creatives };
    let idx = CampaignIndex::build(&s);

    let got = idx.filter(&ctx(Some("CA"), None, &[], &[]));
    assert_eq!(got.len(), 99, "only campaign 3 should be excluded, got {}", got.len());
    assert!(!got.contains(&3));
    assert!(got.contains(&99), "highest-index campaign must not be truncated away");
}

/// (8) Absent request attributes: only campaigns with empty include lists survive.
#[test]
fn absent_attributes_leave_only_untargeted_campaigns() {
    let s = CampaignSnapshot {
        campaigns: vec![
            camp(1, &["US"], &[], &[], &[]),   // targeted
            camp(2, &[], &[], &[], &[]),       // untargeted
            camp(3, &[], &[], &["en"], &[]),   // language-targeted
        ],
        creatives: vec![
            cre(10, 0, AdType::Banner, 0, 0),
            cre(11, 1, AdType::Banner, 0, 0),
            cre(12, 2, AdType::Banner, 0, 0),
        ],
    };
    let idx = CampaignIndex::build(&s);
    // No country and no language on the request.
    let got = idx.filter(&ctx(None, None, &[], &[]));
    assert_eq!(got, vec![1], "only the fully untargeted campaign survives");
}
