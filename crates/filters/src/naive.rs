//! Linear-scan oracle. NOT throwaway — it is the reference implementation the differential
//! property test compares the bitmask index against.
//!
//! Deliberately written straight from the rtbkit semantics with no bitsets, so a bug in the
//! index's set algebra shows up as a disagreement.

use crate::snapshot::{country_key, language_keys, BidContext, CampaignSnapshot};
use std::collections::HashSet;

fn matches_list(list: &[String], keys: &HashSet<String>, normalize_lang: bool) -> bool {
    // Empty include list == match everything (generic_filters.h:541,619,630).
    if list.is_empty() {
        return true;
    }
    list.iter().any(|v| {
        if normalize_lang {
            language_keys(v).iter().any(|k| keys.contains(k))
        } else {
            keys.contains(&country_key(v))
        }
    })
}

fn hits_list(list: &[String], keys: &HashSet<String>, normalize_lang: bool) -> bool {
    list.iter().any(|v| {
        if normalize_lang {
            language_keys(v).iter().any(|k| keys.contains(k))
        } else {
            keys.contains(&country_key(v))
        }
    })
}

pub fn filter(s: &CampaignSnapshot, ctx: &BidContext) -> Vec<usize> {
    let ckeys: HashSet<String> = ctx.country.iter().map(|c| country_key(c)).collect();
    let lkeys: HashSet<String> = ctx
        .language
        .as_deref()
        .map(language_keys)
        .unwrap_or_default()
        .into_iter()
        .collect();

    let fkeys: HashSet<(u32, u32)> = ctx.formats.iter().copied().collect();

    let mut out = Vec::new();
    for (j, cr) in s.creatives.iter().enumerate() {
        let camp = &s.campaigns[cr.owner_campaign_idx];

        // campaign-level location
        if !matches_list(&camp.include_countries, &ckeys, false) { continue; }
        if hits_list(&camp.exclude_countries, &ckeys, false) { continue; }
        // campaign-level language
        if !matches_list(&camp.include_languages, &lkeys, true) { continue; }
        if hits_list(&camp.exclude_languages, &lkeys, true) { continue; }

        // ad size — no-op when the impression declares no format (creative_filters.h:48)
        if !fkeys.is_empty() {
            let wildcard = cr.width == 0 && cr.height == 0; // :51
            if !wildcard && !fkeys.contains(&(cr.width, cr.height)) { continue; }
        }

        // ad type
        if !ctx.ad_types.is_empty() && !ctx.ad_types.contains(&cr.ad_type) { continue; }

        // creative-level narrowing
        if !matches_list(&cr.include_countries, &ckeys, false) { continue; }
        if !matches_list(&cr.include_languages, &lkeys, true) { continue; }

        out.push(j);
    }
    out
}
