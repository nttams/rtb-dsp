//! Postgres -> `CampaignSnapshot` loader.
//!
//! Loads only `status = 'active'` campaigns. The topup worker applies the same filter, so a
//! paused campaign neither bids nor receives a Redis budget hash.

use anyhow::Result;
use filters::snapshot::{AdType, CampaignRow, CampaignSnapshot, CreativeRow};
use sqlx::PgPool;
use std::collections::HashMap;

#[derive(sqlx::FromRow)]
struct CampRec {
    id: i64,
    bid_cpm_micros: i64,
    include_countries: Vec<String>,
    exclude_countries: Vec<String>,
    include_languages: Vec<String>,
    exclude_languages: Vec<String>,
}

#[derive(sqlx::FromRow)]
struct CreRec {
    id: i64,
    campaign_id: i64,
    ad_type: String,
    width: i32,
    height: i32,
    include_countries: Vec<String>,
    include_languages: Vec<String>,
}

fn parse_ad_type(s: &str) -> AdType {
    match s {
        "video" => AdType::Video,
        "native" => AdType::Native,
        _ => AdType::Banner,
    }
}

/// Build a dense-indexed snapshot. Creative rows carry `owner_campaign_idx` pointing into
/// `campaigns`, which is what lets the filter index collapse rtbkit's 2-D `CreativeMatrix`.
pub async fn load_snapshot(pool: &PgPool, _cluster_id: i32) -> Result<CampaignSnapshot> {
    let camps: Vec<CampRec> = sqlx::query_as(
        r#"SELECT id, bid_cpm_micros, include_countries, exclude_countries,
                  include_languages, exclude_languages
           FROM campaigns WHERE status = 'active' ORDER BY id"#,
    )
    .fetch_all(pool)
    .await?;

    let cres: Vec<CreRec> = sqlx::query_as(
        r#"SELECT cr.id, cr.campaign_id, cr.ad_type::text AS ad_type, cr.width, cr.height,
                  cr.include_countries, cr.include_languages
           FROM creatives cr
           JOIN campaigns c ON c.id = cr.campaign_id
           WHERE c.status = 'active'
           ORDER BY cr.id"#,
    )
    .fetch_all(pool)
    .await?;

    let idx_of: HashMap<i64, usize> =
        camps.iter().enumerate().map(|(i, c)| (c.id, i)).collect();

    let campaigns = camps
        .into_iter()
        .map(|c| CampaignRow {
            id: c.id,
            bid_cpm_micros: c.bid_cpm_micros,
            include_countries: c.include_countries,
            exclude_countries: c.exclude_countries,
            include_languages: c.include_languages,
            exclude_languages: c.exclude_languages,
        })
        .collect();

    let creatives = cres
        .into_iter()
        .filter_map(|r| {
            // A creative whose campaign is not in the active set is skipped rather than
            // panicking on a missing index.
            let owner = *idx_of.get(&r.campaign_id)?;
            Some(CreativeRow {
                id: r.id,
                owner_campaign_idx: owner,
                ad_type: parse_ad_type(&r.ad_type),
                width: r.width.max(0) as u32,
                height: r.height.max(0) as u32,
                include_countries: r.include_countries,
                include_languages: r.include_languages,
            })
        })
        .collect();

    Ok(CampaignSnapshot { campaigns, creatives })
}
