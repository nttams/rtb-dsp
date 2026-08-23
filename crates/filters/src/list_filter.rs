//! Port of rtbkit's `ListFilter` (`generic_filters.h:417-465`).
//!
//! An `unordered_map<T, ConfigSet>`; `filter(list)` ORs the sets for every value in the list
//! (`generic_filters.h:441-454`). A key absent from the map contributes nothing.

use crate::config_set::ConfigSet;
use std::collections::HashMap;
use std::hash::Hash;

#[derive(Debug, Clone)]
pub struct ListFilter<K: Eq + Hash> {
    map: HashMap<K, ConfigSet>,
    n: usize,
}

impl<K: Eq + Hash> ListFilter<K> {
    pub fn new(n: usize) -> Self {
        Self { map: HashMap::new(), n }
    }

    pub fn add(&mut self, key: K, config_idx: usize) {
        self.map
            .entry(key)
            .or_insert_with(|| ConfigSet::all_clear(self.n))
            .set(config_idx);
    }

    pub fn is_empty(&self) -> bool {
        self.map.is_empty()
    }

    /// Union of the sets for every supplied key. Always returns a full-width set, so the
    /// `ConfigSet` invariant holds even when no key matches.
    pub fn filter<'a, I>(&self, keys: I) -> ConfigSet
    where
        I: IntoIterator<Item = &'a K>,
        K: 'a,
    {
        let mut out = ConfigSet::all_clear(self.n);
        for k in keys {
            if let Some(s) = self.map.get(k) {
                out.or_with(s);
            }
        }
        out
    }
}
