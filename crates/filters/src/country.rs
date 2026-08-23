//! ISO-3166-1 country-code normalization.
//!
//! # Why this exists
//!
//! OpenRTB specifies `Geo.country` as **ISO-3166-1-alpha-3** (three letters):
//!
//! > "Country code using ISO-3166-1-alpha-3." — OpenRTB 2.6 §3.2.19 (Object: Geo)
//!
//! Campaign targeting, however, is written by humans, who reach for the two-letter codes
//! ("US", "GB"). Before this module the schema stored alpha-2 while the wire delivered
//! alpha-3, so a conformant exchange would have matched **nothing** — every geo-targeted
//! campaign silently dead, with no error anywhere.
//!
//! That bug survived the golden-fixture contract test because the fixtures were hand-written
//! with alpha-3 while the seeder used alpha-2: the two sides agreed with each other and both
//! were checked against the same misreading. It is exactly the failure mode the plan warns
//! about — agreement is not conformance.
//!
//! **Canonical internal form is alpha-3**, matching the wire. Targeting input is normalized on
//! the way in, so an operator may type either.

/// ISO-3166-1 alpha-2 -> alpha-3. Covers the demo's countries plus the common ad markets.
/// Not exhaustive: an unknown 2-letter code is passed through uppercased rather than dropped,
/// so targeting still behaves predictably (it simply will not match a conformant request).
const ALPHA2_TO_ALPHA3: &[(&str, &str)] = &[
    ("US", "USA"), ("GB", "GBR"), ("DE", "DEU"), ("FR", "FRA"), ("JP", "JPN"),
    ("BR", "BRA"), ("CA", "CAN"), ("AU", "AUS"), ("IT", "ITA"), ("ES", "ESP"),
    ("NL", "NLD"), ("SE", "SWE"), ("NO", "NOR"), ("DK", "DNK"), ("FI", "FIN"),
    ("PL", "POL"), ("RU", "RUS"), ("CN", "CHN"), ("IN", "IND"), ("KR", "KOR"),
    ("MX", "MEX"), ("AR", "ARG"), ("ZA", "ZAF"), ("IE", "IRL"), ("CH", "CHE"),
    ("AT", "AUT"), ("BE", "BEL"), ("PT", "PRT"), ("GR", "GRC"), ("TR", "TUR"),
    ("SG", "SGP"), ("HK", "HKG"), ("TW", "TWN"), ("TH", "THA"), ("ID", "IDN"),
    ("MY", "MYS"), ("PH", "PHL"), ("VN", "VNM"), ("NZ", "NZL"), ("IL", "ISR"),
    ("AE", "ARE"), ("SA", "SAU"), ("EG", "EGY"), ("NG", "NGA"), ("KE", "KEN"),
    ("CL", "CHL"), ("CO", "COL"), ("PE", "PER"), ("CZ", "CZE"), ("HU", "HUN"),
    ("RO", "ROU"), ("UA", "UKR"),
];

/// Normalize a country code to the ISO-3166-1 alpha-3 form OpenRTB uses on the wire.
///
/// * 3 letters -> uppercased unchanged (already canonical)
/// * 2 letters -> mapped to alpha-3 when known, else uppercased unchanged
/// * anything else -> uppercased unchanged
pub fn to_alpha3(code: &str) -> String {
    let up = code.trim().to_ascii_uppercase();
    if up.len() == 2 {
        for (a2, a3) in ALPHA2_TO_ALPHA3 {
            if *a2 == up {
                return (*a3).to_string();
            }
        }
    }
    up
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn alpha2_maps_to_alpha3() {
        assert_eq!(to_alpha3("US"), "USA");
        assert_eq!(to_alpha3("gb"), "GBR");
        assert_eq!(to_alpha3("jp"), "JPN");
    }

    #[test]
    fn alpha3_passes_through() {
        assert_eq!(to_alpha3("USA"), "USA");
        assert_eq!(to_alpha3("deu"), "DEU");
    }

    /// The bug this module fixes: a campaign targeting "US" must match a conformant request
    /// carrying "USA".
    #[test]
    fn targeting_alpha2_matches_wire_alpha3() {
        assert_eq!(to_alpha3("US"), to_alpha3("USA"));
        assert_eq!(to_alpha3("BR"), to_alpha3("BRA"));
    }

    #[test]
    fn unknown_code_is_passed_through_not_dropped() {
        assert_eq!(to_alpha3("XX"), "XX");
        assert_eq!(to_alpha3("ZZZ"), "ZZZ");
    }
}
