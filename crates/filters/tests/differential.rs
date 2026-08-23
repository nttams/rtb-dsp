//! Differential property test: the bitmask index must agree with the linear-scan oracle.
//!
//! LIMITS OF THIS TEST (stated deliberately): it proves agreement, not conformance to rtbkit.
//! Both implementations were written by the same author from the same reading of the C++, so a
//! shared misreading passes here at 10,000 cases as easily as at 10. See tests/targeted.rs for
//! the conformance evidence.
//!
//! The generator is tuned so the ConfigSet-truncation class is INSIDE its reach: >= 64
//! campaigns, sparse exclude lists, and high-index campaigns that declare no targeting.

use filters::naive;
use filters::snapshot::*;
use filters::{CampaignIndex, CampaignSnapshot};
use rand::rngs::StdRng;
use rand::{Rng, SeedableRng};

const COUNTRIES: &[&str] = &["US", "GB", "DE", "FR", "JP", "BR", "CA"];
const LANGS: &[&str] = &["en", "de", "fr", "ja", "pt", "en-US", "pt-BR"];
const SIZES: &[(u32, u32)] = &[(300, 250), (728, 90), (320, 50), (160, 600), (970, 250), (300, 600)];
const TYPES: &[AdType] = &[AdType::Banner, AdType::Video, AdType::Native];

fn pick<'a, T>(rng: &mut StdRng, xs: &'a [T]) -> &'a T {
    &xs[rng.gen_range(0..xs.len())]
}

fn gen_snapshot(rng: &mut StdRng) -> CampaignSnapshot {
    // >= 64 campaigns so a sparse per-key bitset is much shorter than the population.
    let n = rng.gen_range(64..120);
    let mut campaigns = Vec::with_capacity(n);
    let mut creatives = Vec::new();

    for i in 0..n {
        // High-index campaigns skew heavily untargeted: that is the population that a
        // truncating intersection would silently drop.
        let untargeted = i > n / 2 && rng.gen_bool(0.7);
        let (inc_c, exc_c, inc_l, exc_l) = if untargeted {
            (vec![], vec![], vec![], vec![])
        } else {
            let inc_c = if rng.gen_bool(0.4) { vec![pick(rng, COUNTRIES).to_string()] } else { vec![] };
            // Sparse excludes: only ~10% of campaigns name one.
            let exc_c = if rng.gen_bool(0.1) { vec![pick(rng, COUNTRIES).to_string()] } else { vec![] };
            let inc_l = if rng.gen_bool(0.3) { vec![pick(rng, LANGS).to_string()] } else { vec![] };
            let exc_l = if rng.gen_bool(0.08) { vec![pick(rng, LANGS).to_string()] } else { vec![] };
            (inc_c, exc_c, inc_l, exc_l)
        };

        campaigns.push(CampaignRow {
            id: i as i64,
            bid_cpm_micros: rng.gen_range(500_000..8_000_000),
            include_countries: inc_c,
            exclude_countries: exc_c,
            include_languages: inc_l,
            exclude_languages: exc_l,
        });

        for _ in 0..rng.gen_range(1..=3) {
            let wildcard = rng.gen_bool(0.15);
            let (w, h) = if wildcard { (0, 0) } else { *pick(rng, SIZES) };
            creatives.push(CreativeRow {
                id: creatives.len() as i64,
                owner_campaign_idx: i,
                ad_type: *pick(rng, TYPES),
                width: w,
                height: h,
                include_countries: if rng.gen_bool(0.15) { vec![pick(rng, COUNTRIES).to_string()] } else { vec![] },
                include_languages: if rng.gen_bool(0.15) { vec![pick(rng, LANGS).to_string()] } else { vec![] },
            });
        }
    }
    CampaignSnapshot { campaigns, creatives }
}

fn gen_ctx(rng: &mut StdRng) -> BidContext {
    BidContext {
        country: if rng.gen_bool(0.9) { Some(pick(rng, COUNTRIES).to_string()) } else { None },
        language: if rng.gen_bool(0.85) { Some(pick(rng, LANGS).to_string()) } else { None },
        ad_types: if rng.gen_bool(0.9) { vec![*pick(rng, TYPES)] } else { vec![] },
        formats: if rng.gen_bool(0.8) { vec![*pick(rng, SIZES)] } else { vec![] },
    }
}

#[test]
fn index_agrees_with_naive_oracle() {
    let mut rng = StdRng::seed_from_u64(0x5EED_7A_68);
    let mut cases = 0usize;
    let mut nonempty = 0usize;

    // 40 populations x 250 requests = 10,000 cases.
    for _ in 0..40 {
        let snap = gen_snapshot(&mut rng);
        let idx = CampaignIndex::build(&snap);
        for _ in 0..250 {
            let ctx = gen_ctx(&mut rng);
            let mut a = idx.filter(&ctx);
            let mut b = naive::filter(&snap, &ctx);
            a.sort_unstable();
            b.sort_unstable();
            assert_eq!(
                a, b,
                "index/oracle disagreement\nctx={:?}\ncampaigns={}\ncreatives={}",
                ctx, snap.campaigns.len(), snap.creatives.len()
            );
            if !a.is_empty() { nonempty += 1; }
            cases += 1;
        }
    }

    assert!(cases >= 10_000, "expected >= 10000 cases, ran {}", cases);
    // Guard against a vacuous pass where everything filters to empty.
    assert!(
        nonempty > cases / 10,
        "generator too strict: only {}/{} cases produced survivors",
        nonempty, cases
    );
}
