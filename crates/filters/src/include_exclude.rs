//! Port of rtbkit's `IncludeExcludeFilter` (`generic_filters.h:538-633`).
//!
//! # The single most important semantic in this crate
//!
//! `emptyIncludes` (`generic_filters.h:541`, seeded at `:549`, `:630`) is the set of configs
//! that declared **no** include list. `filter()` starts from it (`:619`), ORs in the include
//! matches (`:621`), then ANDs with the negated exclude matches (`:624`).
//!
//! **An empty include list means "match EVERYTHING", not "match nothing".**
//!
//! Getting this backwards silently drops every untargeted campaign — the demo seeder
//! deliberately leaves ~20% of campaigns untargeted so a regression here is visible rather
//! than subtle.

use crate::config_set::ConfigSet;
use crate::list_filter::ListFilter;
use std::hash::Hash;

#[derive(Debug, Clone)]
pub struct IncludeExcludeFilter<K: Eq + Hash> {
    includes: ListFilter<K>,
    excludes: ListFilter<K>,
    /// Configs that declared no include list, and therefore match anything.
    empty_includes: ConfigSet,
    n: usize,
}

impl<K: Eq + Hash> IncludeExcludeFilter<K> {
    pub fn new(n: usize) -> Self {
        Self {
            includes: ListFilter::new(n),
            excludes: ListFilter::new(n),
            // Start all-set: a config is assumed to have no include list until one is added.
            empty_includes: ConfigSet::all_set(n),
            n,
        }
    }

    pub fn add_include(&mut self, key: K, config_idx: usize) {
        self.includes.add(key, config_idx);
        // This config DOES declare an include list, so it is no longer a match-anything.
        let mut mask = ConfigSet::all_clear(self.n);
        mask.set(config_idx);
        self.empty_includes.and_with(&mask.negated());
    }

    pub fn add_exclude(&mut self, key: K, config_idx: usize) {
        self.excludes.add(key, config_idx);
    }

    /// `generic_filters.h:617-626`.
    pub fn filter<'a, I>(&self, keys: I) -> ConfigSet
    where
        I: IntoIterator<Item = &'a K> + Clone,
        K: 'a,
    {
        let mut result = self.empty_includes.clone();          // :619
        result.or_with(&self.includes.filter(keys.clone()));   // :621
        result.and_with(&self.excludes.filter(keys).negated()); // :624
        result
    }
}
