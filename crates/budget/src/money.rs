//! Integer micro-dollars (1e-6 USD) end to end. Floating-point money is how overspend
//! assertions become flaky.

/// CPM -> cost of ONE impression, in micro-dollars.
///
/// **Ceiling division on purpose.** Truncating would send `bid_cpm_micros = 999` (a CPM just
/// under $0.001) to `0`, and a zero-amount reserve used to succeed against a zero-budget
/// campaign forever — `reserve.lua` now rejects `amount <= 0`, so truncation would instead
/// make such a campaign silently unbiddable. Ceiling keeps every positive CPM costing at
/// least 1 micro-dollar. `rtbdsp_common/money.py` must agree exactly; a contract test asserts it.
pub fn cpm_to_per_imp_micros(cpm_micros: i64) -> i64 {
    if cpm_micros <= 0 {
        return 0;
    }
    (cpm_micros + 999) / 1000
}

/// Inverse, for reporting only. Not used in the bid path.
pub fn per_imp_to_cpm_micros(per_imp_micros: i64) -> i64 {
    per_imp_micros.saturating_mul(1000)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ceiling_division_boundaries() {
        assert_eq!(cpm_to_per_imp_micros(0), 0);
        assert_eq!(cpm_to_per_imp_micros(1), 1, "any positive CPM costs >= 1 micro-dollar");
        assert_eq!(cpm_to_per_imp_micros(999), 1, "sub-$0.001 CPM must not truncate to 0");
        assert_eq!(cpm_to_per_imp_micros(1000), 1);
        assert_eq!(cpm_to_per_imp_micros(1001), 2);
        assert_eq!(cpm_to_per_imp_micros(2_500_000), 2_500, "$2.50 CPM -> 2500 micros/imp");
        assert_eq!(cpm_to_per_imp_micros(8_000_000), 8_000);
    }
}
