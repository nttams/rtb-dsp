//! Port of rtbkit's `ConfigSet` (`rtbkit/rtbkit/common/filter.h:44-104`).
//!
//! # The `defaultValue` problem — read before changing anything here
//!
//! rtbkit's `ConfigSet` carries a `defaultValue` that is NOT cosmetic:
//!
//! * `operator&=` (`filter.h:116-128`) calls `expand(other.size())`, then ANDs the **tail**
//!   words — those beyond `other`'s length — against `other.defaultValue`.
//! * `negate()` (`filter.h:153-160`) flips `defaultValue` alongside every word.
//!
//! Together these give `ConfigSet` conceptually-infinite length. `FixedBitSet`'s `BitAnd`
//! yields the **minimum** of the two lengths instead.
//!
//! Concretely, if we ignored this: a `ListFilter` entry for country `"CA"` only has bits for
//! campaigns that named `CA`, so its bitset length would be the highest such index — say 12 —
//! in a 100-campaign index. `excludes.filter("CA").negate()` would be 12 bits wide, and
//! `configs &= that` would truncate to 12 bits, **silently dropping campaigns 13..99 from
//! every request whose country appears in any exclude list.**
//!
//! # The invariant that replaces it
//!
//! Every `ConfigSet` is constructed with `FixedBitSet::with_capacity(n)` for the SAME `n`
//! (the campaign count) and is **never grown**. There are exactly two constructors and both
//! take `n`. Every binary operation asserts equal length. Under this invariant there is no
//! tail, so `defaultValue` has nothing to initialize and can be dropped.

use fixedbitset::FixedBitSet;

macro_rules! define_set {
    ($name:ident, $what:literal) => {
        #[doc = concat!("A bitset over ", $what, " indices. Fixed width; see module docs.")]
        #[derive(Debug, Clone, PartialEq, Eq)]
        pub struct $name {
            bits: FixedBitSet,
            n: usize,
        }

        impl $name {
            pub fn all_clear(n: usize) -> Self {
                Self { bits: FixedBitSet::with_capacity(n), n }
            }

            pub fn all_set(n: usize) -> Self {
                let mut bits = FixedBitSet::with_capacity(n);
                bits.insert_range(..);
                Self { bits, n }
            }

            #[inline]
            pub fn len(&self) -> usize { self.n }

            #[inline]
            pub fn is_empty(&self) -> bool { self.bits.count_ones(..) == 0 }

            #[inline]
            pub fn count(&self) -> usize { self.bits.count_ones(..) }

            #[inline]
            pub fn set(&mut self, i: usize) {
                debug_assert!(i < self.n, "index {} out of range for width {}", i, self.n);
                self.bits.insert(i);
            }

            #[inline]
            pub fn test(&self, i: usize) -> bool {
                i < self.n && self.bits.contains(i)
            }

            pub fn iter_ones(&self) -> impl Iterator<Item = usize> + '_ { self.bits.ones() }

            /// In-place intersection. Both operands MUST be the same width — that is the
            /// invariant standing in for rtbkit's `defaultValue` tail handling.
            pub fn and_with(&mut self, other: &Self) {
                debug_assert_eq!(self.n, other.n, "width mismatch: {} vs {}", self.n, other.n);
                self.bits.intersect_with(&other.bits);
            }

            pub fn or_with(&mut self, other: &Self) {
                debug_assert_eq!(self.n, other.n, "width mismatch: {} vs {}", self.n, other.n);
                self.bits.union_with(&other.bits);
            }

            /// Complement within the fixed width. rtbkit also flips `defaultValue`
            /// (`filter.h:155`); with a fixed width there is no tail to flip.
            pub fn negate(&mut self) {
                self.bits.toggle_range(..);
            }

            pub fn negated(&self) -> Self {
                let mut c = self.clone();
                c.negate();
                c
            }
        }
    };
}

define_set!(ConfigSet, "campaign");
define_set!(CreativeSet, "creative");

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn negate_preserves_width_and_flips_all() {
        let s = ConfigSet::all_clear(100);
        let n = s.negated();
        assert_eq!(n.len(), 100);
        assert_eq!(n.count(), 100);
    }

    /// The regression this whole module exists for. A sparse set that names only a low index
    /// must not truncate a wide set when intersected.
    #[test]
    fn sparse_negated_set_does_not_truncate_high_indices() {
        let n = 100;
        let mut excluded = ConfigSet::all_clear(n);
        excluded.set(3); // only campaign 3 excludes this country

        let mut survivors = ConfigSet::all_set(n);
        survivors.and_with(&excluded.negated());

        assert_eq!(survivors.count(), 99, "campaigns 4..99 must survive");
        assert!(!survivors.test(3));
        assert!(survivors.test(99), "highest-index campaign must not be truncated away");
    }
}
