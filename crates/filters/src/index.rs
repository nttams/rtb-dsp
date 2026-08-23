//! The bitmask filter index — rtbkit's set-intersection model rather than a per-campaign scan.
//!
//! Filter order follows `priority.h:22-30`:
//! Location (0x1000) < Language (0x1100) < CreativeFormat (0x2000) < CreativeLocation (0x2100)
//! < CreativeLanguage (0x2300). We short-circuit the moment the surviving set is empty.
//!
//! # Deviation from `CreativeMatrix`
//!
//! rtbkit uses a 2-D `CreativeMatrix[creative][config]` (`filter.h:230-280`) because a creative
//! index is relative to an agent config, so one creative slot can belong to many configs. Our
//! model is flat — a creative belongs to exactly one campaign — so a flat creative bitset plus
//! an owner map is provably equivalent and about half the code. `narrowCreativesForImp` /
//! `narrowAllCreatives` (`filter.h:407-420`) collapse to a bitwise AND.

use crate::config_set::{ConfigSet, CreativeSet};
use crate::include_exclude::IncludeExcludeFilter;
use crate::list_filter::ListFilter;
use crate::snapshot::{country_key, language_keys, AdType, BidContext, CampaignSnapshot};

/// `creative_filters.h:69` — key is `u32(width << 16 | height)`.
#[inline]
fn format_key(w: u32, h: u32) -> u32 {
    (w << 16) | (h & 0xFFFF)
}

const WILDCARD: u32 = 0; // Format(0,0) matches anything — creative_filters.h:51

pub struct CampaignIndex {
    n_campaigns: usize,
    n_creatives: usize,

    // Campaign-level (static_filters.h)
    location: IncludeExcludeFilter<String>,
    language: IncludeExcludeFilter<String>,

    // Creative-level (creative_filters.h)
    creative_format: ListFilter<u32>,
    creative_ad_type: ListFilter<AdType>,
    creative_location: IncludeExcludeFilter<String>,
    creative_language: IncludeExcludeFilter<String>,

    owner: Vec<usize>, // creative idx -> campaign idx
}

impl CampaignIndex {
    pub fn build(s: &CampaignSnapshot) -> Self {
        let nc = s.campaigns.len();
        let nk = s.creatives.len();

        let mut location = IncludeExcludeFilter::new(nc);
        let mut language = IncludeExcludeFilter::new(nc);

        for (i, c) in s.campaigns.iter().enumerate() {
            for v in &c.include_countries {
                location.add_include(country_key(v), i);
            }
            for v in &c.exclude_countries {
                location.add_exclude(country_key(v), i);
            }
            for v in &c.include_languages {
                for k in language_keys(v) {
                    language.add_include(k, i);
                }
            }
            for v in &c.exclude_languages {
                for k in language_keys(v) {
                    language.add_exclude(k, i);
                }
            }
        }

        // Creative-level filters are indexed by CREATIVE index, so their width is nk.
        let mut creative_format = ListFilter::new(nk);
        let mut creative_ad_type = ListFilter::new(nk);
        let mut creative_location = IncludeExcludeFilter::new(nk);
        let mut creative_language = IncludeExcludeFilter::new(nk);
        let mut owner = Vec::with_capacity(nk);

        for (j, cr) in s.creatives.iter().enumerate() {
            owner.push(cr.owner_campaign_idx);
            creative_format.add(format_key(cr.width, cr.height), j);
            creative_ad_type.add(cr.ad_type, j);
            for v in &cr.include_countries {
                creative_location.add_include(country_key(v), j);
            }
            for v in &cr.include_languages {
                for k in language_keys(v) {
                    creative_language.add_include(k, j);
                }
            }
        }

        Self {
            n_campaigns: nc,
            n_creatives: nk,
            location,
            language,
            creative_format,
            creative_ad_type,
            creative_location,
            creative_language,
            owner,
        }
    }

    /// Returns surviving CREATIVE indices.
    pub fn filter(&self, ctx: &BidContext) -> Vec<usize> {
        if self.n_creatives == 0 {
            return Vec::new();
        }

        // --- campaign-level, in priority order ---
        let country_keys: Vec<String> = ctx.country.iter().map(|c| country_key(c)).collect();
        let lang_keys: Vec<String> = ctx
            .language
            .as_deref()
            .map(language_keys)
            .unwrap_or_default();

        let mut configs = self.location.filter(country_keys.iter());
        if configs.is_empty() {
            return Vec::new();
        }
        configs.and_with(&self.language.filter(lang_keys.iter()));
        if configs.is_empty() {
            return Vec::new();
        }

        // --- creative-level ---
        // Start from creatives whose owning campaign survived.
        let mut creatives = CreativeSet::all_clear(self.n_creatives);
        for (j, &owner_idx) in self.owner.iter().enumerate() {
            if configs.test(owner_idx) {
                creatives.set(j);
            }
        }
        if creatives.is_empty() {
            return Vec::new();
        }

        // Ad size. `creative_filters.h:48` — a NO-OP when the impression declares no format.
        if !ctx.formats.is_empty() {
            let mut keys: Vec<u32> = ctx.formats.iter().map(|&(w, h)| format_key(w, h)).collect();
            keys.push(WILDCARD); // Format(0,0) creatives match any size — :51
            let matched = self.creative_format.filter(keys.iter());
            creatives.and_with(&as_creative_set(&matched, self.n_creatives));
            if creatives.is_empty() {
                return Vec::new();
            }
        }

        // Ad type. No rtbkit counterpart among the four — rtbkit resolves banner/video via its
        // exchange connectors, which are out of scope. Multiple imp objects => union.
        if !ctx.ad_types.is_empty() {
            let matched = self.creative_ad_type.filter(ctx.ad_types.iter());
            creatives.and_with(&as_creative_set(&matched, self.n_creatives));
            if creatives.is_empty() {
                return Vec::new();
            }
        }

        // Creative-level narrowing (`filter.h:407-420` collapses to an AND).
        creatives.and_with(&as_creative_set(
            &self.creative_location.filter(country_keys.iter()),
            self.n_creatives,
        ));
        if creatives.is_empty() {
            return Vec::new();
        }
        creatives.and_with(&as_creative_set(
            &self.creative_language.filter(lang_keys.iter()),
            self.n_creatives,
        ));

        creatives.iter_ones().collect()
    }

    pub fn owner_of(&self, creative_idx: usize) -> usize {
        self.owner[creative_idx]
    }

    pub fn n_campaigns(&self) -> usize {
        self.n_campaigns
    }
}

/// The creative-level filters are built over creative indices, so their `ConfigSet` is already
/// creative-width; this reinterprets it as a `CreativeSet` without changing any bit.
fn as_creative_set(c: &ConfigSet, n: usize) -> CreativeSet {
    debug_assert_eq!(c.len(), n);
    let mut out = CreativeSet::all_clear(n);
    for i in c.iter_ones() {
        out.set(i);
    }
    out
}
