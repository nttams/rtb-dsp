use axum::{
    body::Bytes,
    extract::State,
    http::StatusCode,
    response::{IntoResponse, Response},
};
use std::sync::Arc;
use std::time::Instant;

use crate::auction::{self, NoBid};
use crate::metrics::Metrics;
use crate::state::AppState;

pub async fn healthz() -> &'static str {
    "ok"
}

pub async fn metrics(State(st): State<Arc<AppState>>) -> Response {
    (
        StatusCode::OK,
        [("content-type", "text/plain; version=0.0.4")],
        st.metrics.render(),
    )
        .into_response()
}

/// `POST /openrtb2/bid`.
///
/// Response codes are deliberately distinct:
/// * **204** — a legitimate no-bid (no match, below floor, no budget). AC-3.
/// * **400** — the body could not be deserialized.
///
/// Returning 204 for malformed JSON (the original design) would make a cross-language wire
/// break indistinguishable from "nothing matched": the engine would 204 every request, the
/// dashboard would show zero bids across all 100 campaigns, and the operator would go debug
/// the filter index. AC-3 only requires a no-MATCH to be a well-formed no-bid; it does not
/// require an undeserializable body to look the same.
pub async fn bid(State(st): State<Arc<AppState>>, body: Bytes) -> Response {
    let started = Instant::now();
    Metrics::inc(&st.metrics.requests);

    let req: wire::BidRequest = match serde_json::from_slice(&body) {
        Ok(r) => r,
        Err(e) => {
            Metrics::inc(&st.metrics.bad_request);
            let prefix: String = String::from_utf8_lossy(&body).chars().take(200).collect();
            tracing::warn!(error = %e, body_prefix = %prefix, "undeserializable bid request");
            return StatusCode::BAD_REQUEST.into_response();
        }
    };

    // MVP bids on imp[0] only. rtbkit narrows creatives per impression (filter.h:382, :407);
    // the mock exchange is constrained to one impression per request so this is never
    // silently exercised.
    let Some(imp) = req.imp.first() else {
        Metrics::inc(&st.metrics.bad_request);
        return StatusCode::BAD_REQUEST.into_response();
    };

    let bidfloor = match wire::imp_bidfloor_micros(imp) {
        Some(f) => Some(f),
        None => {
            Metrics::inc(&st.metrics.nonusd_floor);
            auction::record_nobid(&st.metrics, NoBid::BelowFloor);
            return StatusCode::NO_CONTENT.into_response();
        }
    };

    let ctx = filters::BidContext {
        country: wire::request_country(&req),
        language: wire::request_language(&req),
        ad_types: wire::imp_ad_type(imp),
        formats: wire::imp_formats(imp),
    };

    let bundle = st.index.load();
    let outcome = auction::run(
        &bundle.snapshot,
        &bundle.index,
        &ctx,
        bidfloor,
        &st.budget,
        st.cfg.reservation_ttl_ms,
        st.cfg.max_budget_fallback,
        &st.metrics,
    )
    .await;

    let resp = match outcome {
        Ok(win) => {
            Metrics::inc(&st.metrics.bids);
            let body = wire::build_bid_response(
                &req.id,
                &imp.id,
                &win.bid_id,
                win.price_cpm_micros,
                win.creative_id,
                win.campaign_id,
                &win.reservation_id,
                &st.cfg.cluster_name,
            );
            (StatusCode::OK, axum::Json(body)).into_response()
        }
        Err(reason) => {
            auction::record_nobid(&st.metrics, reason);
            StatusCode::NO_CONTENT.into_response()
        }
    };

    st.metrics
        .observe_latency_us(started.elapsed().as_micros() as u64);
    resp
}
