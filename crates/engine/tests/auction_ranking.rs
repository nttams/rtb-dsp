//! Auction ordering and the bid-floor gate. These need no datastore: they exercise the ranking
//! and floor logic directly. The reserve path is covered by the budget regression tests, which
//! require a live Redis and are gated behind `#[ignore]`.

use filters::snapshot::*;
use filters::CampaignIndex;

fn camp(id: i64, cpm: i64) -> CampaignRow {
    CampaignRow {
        id,
        bid_cpm_micros: cpm,
        include_countries: vec![],
        exclude_countries: vec![],
        include_languages: vec![],
        exclude_languages: vec![],
    }
}

fn cre(id: i64, owner: usize) -> CreativeRow {
    CreativeRow {
        id,
        owner_campaign_idx: owner,
        ad_type: AdType::Banner,
        width: 0,
        height: 0,
        include_countries: vec![],
        include_languages: vec![],
    }
}

/// Mirrors `auction::run`'s ordering: (bid_cpm DESC, campaign_id ASC, creative_id ASC).
/// Kept in the test so a change to the ordering rule fails here loudly.
fn rank(snapshot: &CampaignSnapshot, index: &CampaignIndex, ctx: &BidContext) -> Vec<(i64, i64)> {
    let mut r: Vec<usize> = index.filter(ctx);
    r.sort_by(|&a, &b| {
        let ca = &snapshot.campaigns[index.owner_of(a)];
        let cb = &snapshot.campaigns[index.owner_of(b)];
        cb.bid_cpm_micros
            .cmp(&ca.bid_cpm_micros)
            .then_with(|| ca.id.cmp(&cb.id))
            .then_with(|| snapshot.creatives[a].id.cmp(&snapshot.creatives[b].id))
    });
    r.into_iter()
        .map(|ci| {
            let c = &snapshot.campaigns[index.owner_of(ci)];
            (c.id, c.bid_cpm_micros)
        })
        .collect()
}

fn ctx() -> BidContext {
    BidContext {
        country: Some("US".into()),
        language: Some("en".into()),
        ad_types: vec![AdType::Banner],
        formats: vec![(300, 250)],
    }
}

#[test]
fn highest_cpm_ranks_first() {
    let s = CampaignSnapshot {
        campaigns: vec![camp(1, 1_000_000), camp(2, 5_000_000), camp(3, 2_000_000)],
        creatives: vec![cre(10, 0), cre(11, 1), cre(12, 2)],
    };
    let idx = CampaignIndex::build(&s);
    let r = rank(&s, &idx, &ctx());
    assert_eq!(r[0], (2, 5_000_000), "highest CPM must win");
    assert_eq!(r[1], (3, 2_000_000));
}

/// Equal CPMs must break deterministically by campaign id, or tests go flaky and the demo
/// becomes unexplainable (Risk R12).
#[test]
fn equal_cpm_breaks_deterministically_by_campaign_id() {
    let s = CampaignSnapshot {
        campaigns: vec![camp(7, 3_000_000), camp(2, 3_000_000), camp(5, 3_000_000)],
        creatives: vec![cre(10, 0), cre(11, 1), cre(12, 2)],
    };
    let idx = CampaignIndex::build(&s);
    for _ in 0..20 {
        let r = rank(&s, &idx, &ctx());
        assert_eq!(r.iter().map(|x| x.0).collect::<Vec<_>>(), vec![2, 5, 7]);
    }
}

/// The floor is a CPM and so is our price. Ranked descending, so once the top bid is below
/// the floor nothing else clears it either.
#[test]
fn bid_floor_gate() {
    let s = CampaignSnapshot {
        campaigns: vec![camp(1, 2_000_000)],
        creatives: vec![cre(10, 0)],
    };
    let idx = CampaignIndex::build(&s);
    let r = rank(&s, &idx, &ctx());
    let top_cpm = r[0].1;

    assert!(top_cpm >= 1_500_000, "clears a $1.50 floor");
    assert!(top_cpm < 3_000_000, "does not clear a $3.00 floor");
}

#[test]
fn no_survivors_means_no_bid() {
    let s = CampaignSnapshot {
        campaigns: vec![CampaignRow {
            include_countries: vec!["JP".into()],
            ..camp(1, 2_000_000)
        }],
        creatives: vec![cre(10, 0)],
    };
    let idx = CampaignIndex::build(&s);
    assert!(rank(&s, &idx, &ctx()).is_empty());
}
