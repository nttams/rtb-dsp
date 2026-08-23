"""campaign_app — FastAPI + Jinja2 CRUD for campaigns/creatives, dashboard, demo seeding.

AC-21..AC-28. No client-side framework: every response is server-rendered HTML, and
`templates/` contains no `<script>` tag and no `onclick=` attribute (AC-23, enforced by
test_no_client_js.py).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from psycopg.rows import dict_row

from campaign_app import seeder, stats
from rtbdsp_common import metrics
from rtbdsp_common.db import make_pool
from rtbdsp_common.redis_client import ClusterRegistry
from rtbdsp_common.settings import bind_host_port, get_settings

logger = logging.getLogger("campaign_app")

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

seed_runs_total = metrics.counter("campaign_app_seed_runs_total", "Demo seed runs")

# Contract name per docs/metrics.md. Derived from the same Redis budget/stats hashes the
# dashboard reads (AC-44) -- computed fresh on each scrape rather than tracked in process
# memory, so Grafana and the dashboard cannot disagree.
budget_exhausted_gauge = metrics.gauge(
    "rtbdsp_budget_exhausted_campaigns",
    "Campaigns that can no longer afford one impression (remaining < ceil(bid_cpm/1000))"
)


def _parse_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def _dollars_to_micros(s: str) -> int:
    return int(round(float(s) * 1_000_000))


@asynccontextmanager
async def _lifespan(app: FastAPI):
    settings = get_settings()
    if app.state.pool is None:
        app.state.pool = make_pool(settings.database_url)
        await app.state.pool.open()
    if app.state.registry is None:
        app.state.registry = ClusterRegistry(app.state.pool)
        await app.state.registry.refresh()
    try:
        yield
    finally:
        if app.state.registry is not None:
            await app.state.registry.close()
        if app.state.owns_pool and app.state.pool is not None:
            await app.state.pool.close()


def create_app(pool=None, registry: ClusterRegistry | None = None) -> FastAPI:
    app = FastAPI(title="campaign-app", lifespan=_lifespan)
    app.state.pool = pool
    app.state.registry = registry
    app.state.owns_pool = pool is None

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics_endpoint() -> Response:
        settings = get_settings()
        if app.state.pool is not None and app.state.registry is not None:
            rows = await stats.dashboard_rows(app.state.pool, app.state.registry, settings.cluster_name)
            budget_exhausted_gauge.set(sum(1 for r in rows if r["exhausted"]))
        return Response(content=metrics.metrics_bytes(), media_type=metrics.CONTENT_TYPE_LATEST)

    @app.get("/")
    async def root() -> RedirectResponse:
        return RedirectResponse("/campaigns")

    # --- campaigns ---

    @app.get("/campaigns")
    async def list_campaigns(request: Request):
        pool = app.state.pool
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("SELECT * FROM campaigns ORDER BY id")
                rows = await cur.fetchall()
        return templates.TemplateResponse(
            request, "campaigns.html",
            {"campaigns": rows})

    @app.get("/campaigns/new")
    async def new_campaign_form(request: Request):
        settings = get_settings()
        return templates.TemplateResponse(
            request, "campaign_form.html",
            {"campaign": None, "allocated_micros": None,
             "cluster_name": settings.cluster_name, "error": None},
        )

    @app.post("/campaigns")
    async def create_campaign(
        request: Request,
        name: str = Form(...),
        status: str = Form("active"),
        bid_cpm: str = Form(...),
        total_budget: str = Form(...),
        allocated_budget: str = Form(...),
        include_countries: str = Form(""),
        exclude_countries: str = Form(""),
        include_languages: str = Form(""),
        exclude_languages: str = Form(""),
    ):
        settings = get_settings()
        bid_cpm_micros = _dollars_to_micros(bid_cpm)
        total_budget_micros = _dollars_to_micros(total_budget)
        allocated_micros = _dollars_to_micros(allocated_budget)

        if allocated_micros > total_budget_micros:
            return templates.TemplateResponse(
            request, "campaign_form.html",
            {"campaign": None, "allocated_micros": allocated_micros,
                 "cluster_name": settings.cluster_name,
                 "error": "Allocation exceeds total budget."},
                status_code=400,
            )

        pool = app.state.pool
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO campaigns (name, status, bid_cpm_micros, total_budget_micros,
                                            include_countries, exclude_countries,
                                            include_languages, exclude_languages)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        name, status, bid_cpm_micros, total_budget_micros,
                        _parse_list(include_countries), _parse_list(exclude_countries),
                        _parse_list(include_languages), _parse_list(exclude_languages),
                    ),
                )
                campaign_id = (await cur.fetchone())[0]
                await cur.execute(
                    """
                    INSERT INTO budget_allocations (campaign_id, cluster_id, allocated_micros, spent_micros)
                    VALUES (%s, %s, %s, 0)
                    """,
                    (campaign_id, settings.cluster_id, allocated_micros),
                )
            await conn.commit()

        return RedirectResponse("/campaigns", status_code=303)

    @app.get("/campaigns/{campaign_id}/edit")
    async def edit_campaign_form(request: Request, campaign_id: int):
        settings = get_settings()
        pool = app.state.pool
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("SELECT * FROM campaigns WHERE id = %s", (campaign_id,))
                campaign = await cur.fetchone()
                if campaign is None:
                    raise HTTPException(status_code=404, detail="campaign not found")
                await cur.execute(
                    "SELECT allocated_micros FROM budget_allocations WHERE campaign_id = %s AND cluster_id = %s",
                    (campaign_id, settings.cluster_id),
                )
                alloc_row = await cur.fetchone()
                allocated_micros = alloc_row["allocated_micros"] if alloc_row else 0
        return templates.TemplateResponse(
            request, "campaign_form.html",
            {"campaign": campaign, "allocated_micros": allocated_micros,
             "cluster_name": settings.cluster_name, "error": None},
        )

    @app.post("/campaigns/{campaign_id}")
    async def update_campaign(
        request: Request,
        campaign_id: int,
        name: str = Form(...),
        status: str = Form("active"),
        bid_cpm: str = Form(...),
        total_budget: str = Form(...),
        allocated_budget: str = Form(...),
        include_countries: str = Form(""),
        exclude_countries: str = Form(""),
        include_languages: str = Form(""),
        exclude_languages: str = Form(""),
    ):
        settings = get_settings()
        bid_cpm_micros = _dollars_to_micros(bid_cpm)
        total_budget_micros = _dollars_to_micros(total_budget)
        allocated_micros = _dollars_to_micros(allocated_budget)

        pool = app.state.pool
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                # Read-check-write: lock the parent campaign row so two concurrent edits
                # cannot both pass the SUM(allocations) <= total_budget check.
                await cur.execute(
                    "SELECT id, total_budget_micros FROM campaigns WHERE id = %s FOR UPDATE",
                    (campaign_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    raise HTTPException(status_code=404, detail="campaign not found")

                await cur.execute(
                    "SELECT COALESCE(SUM(allocated_micros), 0) AS s FROM budget_allocations "
                    "WHERE campaign_id = %s AND cluster_id != %s",
                    (campaign_id, settings.cluster_id),
                )
                other_sum = (await cur.fetchone())["s"]

                if other_sum + allocated_micros > total_budget_micros:
                    await conn.rollback()
                    campaign = dict(row)
                    campaign.update(
                        {
                            "name": name, "status": status,
                            "bid_cpm_micros": bid_cpm_micros,
                            "total_budget_micros": total_budget_micros,
                            "include_countries": _parse_list(include_countries),
                            "exclude_countries": _parse_list(exclude_countries),
                            "include_languages": _parse_list(include_languages),
                            "exclude_languages": _parse_list(exclude_languages),
                        }
                    )
                    return templates.TemplateResponse(
            request, "campaign_form.html",
            {"campaign": campaign, "allocated_micros": allocated_micros,
                         "cluster_name": settings.cluster_name,
                         "error": "Sum of cluster allocations would exceed total budget."},
                        status_code=400,
                    )

                await cur.execute(
                    """
                    UPDATE campaigns SET name=%s, status=%s, bid_cpm_micros=%s, total_budget_micros=%s,
                        include_countries=%s, exclude_countries=%s,
                        include_languages=%s, exclude_languages=%s, updated_at=now()
                    WHERE id=%s
                    """,
                    (
                        name, status, bid_cpm_micros, total_budget_micros,
                        _parse_list(include_countries), _parse_list(exclude_countries),
                        _parse_list(include_languages), _parse_list(exclude_languages),
                        campaign_id,
                    ),
                )
                await cur.execute(
                    """
                    INSERT INTO budget_allocations (campaign_id, cluster_id, allocated_micros, spent_micros)
                    VALUES (%s, %s, %s, 0)
                    ON CONFLICT (campaign_id, cluster_id)
                    DO UPDATE SET allocated_micros = EXCLUDED.allocated_micros, updated_at = now()
                    """,
                    (campaign_id, settings.cluster_id, allocated_micros),
                )
            await conn.commit()

        return RedirectResponse("/campaigns", status_code=303)

    @app.post("/campaigns/{campaign_id}/delete")
    async def delete_campaign(campaign_id: int):
        pool = app.state.pool
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM campaigns WHERE id = %s", (campaign_id,))
            await conn.commit()
        return RedirectResponse("/campaigns", status_code=303)

    # --- creatives ---

    @app.get("/campaigns/{campaign_id}/creatives")
    async def list_creatives(request: Request, campaign_id: int):
        pool = app.state.pool
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("SELECT * FROM campaigns WHERE id = %s", (campaign_id,))
                campaign = await cur.fetchone()
                if campaign is None:
                    raise HTTPException(status_code=404, detail="campaign not found")
                await cur.execute(
                    "SELECT * FROM creatives WHERE campaign_id = %s ORDER BY id", (campaign_id,)
                )
                creatives = await cur.fetchall()
        return templates.TemplateResponse(
            request, "creatives.html",
            {"campaign": campaign, "creatives": creatives}
        )

    @app.get("/campaigns/{campaign_id}/creatives/new")
    async def new_creative_form(request: Request, campaign_id: int):
        pool = app.state.pool
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("SELECT * FROM campaigns WHERE id = %s", (campaign_id,))
                campaign = await cur.fetchone()
                if campaign is None:
                    raise HTTPException(status_code=404, detail="campaign not found")
        return templates.TemplateResponse(
            request, "creative_form.html",
            {"campaign": campaign, "creative": None, "error": None}
        )

    @app.post("/campaigns/{campaign_id}/creatives")
    async def create_creative(
        request: Request,
        campaign_id: int,
        name: str = Form(...),
        ad_type: str = Form(...),
        width: str = Form("0"),
        height: str = Form("0"),
        include_countries: str = Form(""),
        include_languages: str = Form(""),
    ):
        pool = app.state.pool
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO creatives (campaign_id, name, ad_type, width, height,
                                            include_countries, include_languages)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        campaign_id, name, ad_type, int(width), int(height),
                        _parse_list(include_countries), _parse_list(include_languages),
                    ),
                )
            await conn.commit()
        return RedirectResponse(f"/campaigns/{campaign_id}/creatives", status_code=303)

    @app.get("/campaigns/{campaign_id}/creatives/{creative_id}/edit")
    async def edit_creative_form(request: Request, campaign_id: int, creative_id: int):
        pool = app.state.pool
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("SELECT * FROM campaigns WHERE id = %s", (campaign_id,))
                campaign = await cur.fetchone()
                if campaign is None:
                    raise HTTPException(status_code=404, detail="campaign not found")
                await cur.execute(
                    "SELECT * FROM creatives WHERE id = %s AND campaign_id = %s",
                    (creative_id, campaign_id),
                )
                creative = await cur.fetchone()
                if creative is None:
                    raise HTTPException(status_code=404, detail="creative not found")
        return templates.TemplateResponse(
            request, "creative_form.html",
            {"campaign": campaign, "creative": creative, "error": None}
        )

    @app.post("/campaigns/{campaign_id}/creatives/{creative_id}")
    async def update_creative(
        request: Request,
        campaign_id: int,
        creative_id: int,
        name: str = Form(...),
        ad_type: str = Form(...),
        width: str = Form("0"),
        height: str = Form("0"),
        include_countries: str = Form(""),
        include_languages: str = Form(""),
    ):
        pool = app.state.pool
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE creatives SET name=%s, ad_type=%s, width=%s, height=%s,
                        include_countries=%s, include_languages=%s, updated_at=now()
                    WHERE id=%s AND campaign_id=%s
                    """,
                    (
                        name, ad_type, int(width), int(height),
                        _parse_list(include_countries), _parse_list(include_languages),
                        creative_id, campaign_id,
                    ),
                )
            await conn.commit()
        return RedirectResponse(f"/campaigns/{campaign_id}/creatives", status_code=303)

    @app.post("/campaigns/{campaign_id}/creatives/{creative_id}/delete")
    async def delete_creative(campaign_id: int, creative_id: int):
        pool = app.state.pool
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM creatives WHERE id = %s AND campaign_id = %s", (creative_id, campaign_id)
                )
            await conn.commit()
        return RedirectResponse(f"/campaigns/{campaign_id}/creatives", status_code=303)

    # --- dashboard + demo seed ---

    @app.get("/dashboard")
    async def dashboard(request: Request):
        settings = get_settings()
        rows = await stats.dashboard_rows(app.state.pool, app.state.registry, settings.cluster_name)
        return templates.TemplateResponse(
            request, "dashboard.html",
            {"rows": rows})

    @app.post("/demo/seed")
    async def demo_seed():
        settings = get_settings()
        await seeder.seed_demo(
            app.state.pool,
            app.state.registry,
            settings.cluster_id,
            settings.cluster_name,
            seed_min=settings.seed_impressions_min,
            seed_max=settings.seed_impressions_max,
        )
        seed_runs_total.inc()
        return RedirectResponse("/dashboard", status_code=303)

    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    host, port = bind_host_port(settings.campaign_app_bind)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()
