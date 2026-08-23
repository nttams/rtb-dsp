-- TAM-68 MVP bidding engine — initial schema.
--
-- IDEMPOTENT BY CONSTRUCTION. The `migrate` init service runs on every `docker compose up`,
-- so a non-idempotent migration makes the stack un-restartable against an existing volume:
-- the second `make demo` aborts with "relation already exists" and every dependent service
-- refuses to start. Enum creation is wrapped in an exception-swallowing DO block because
-- CREATE TYPE has no IF NOT EXISTS form.
-- All money columns are BIGINT micro-dollars (1e-6 USD). $2.50 CPM -> 2_500_000.

CREATE TABLE IF NOT EXISTS clusters (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    redis_url   TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$ BEGIN
    CREATE TYPE campaign_status AS ENUM ('active', 'paused');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE ad_type AS ENUM ('banner', 'video', 'native');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS campaigns (
    id                  BIGSERIAL PRIMARY KEY,
    name                TEXT NOT NULL,
    status              campaign_status NOT NULL DEFAULT 'active',
    bid_cpm_micros      BIGINT NOT NULL CHECK (bid_cpm_micros > 0),
    total_budget_micros BIGINT NOT NULL CHECK (total_budget_micros >= 0),
    -- Targeting. An EMPTY array means "match all" — rtbkit IncludeExcludeFilter::emptyIncludes,
    -- generic_filters.h:541,619,630. Getting this backwards silently drops every untargeted campaign.
    --
    -- WIRE FORMAT (verified against the IAB OpenRTB 2.6 spec):
    --   countries: ISO-3166-1 ALPHA-3, uppercase ('USA','GBR') — "Country code using
    --     ISO-3166-1-alpha-3." Storing alpha-2 here while a conformant exchange sends alpha-3
    --     silently kills every geo-targeted campaign. filters::country::to_alpha3 normalizes
    --     alpha-2 input, so an operator may type 'US', but alpha-3 is the canonical stored form.
    --   languages: ISO-639-1 alpha-2, lowercase ('en','fr') — "Browser language using
    --     ISO-639-1-alpha-2." The BCP-47 form ('en-US') is Device.langb, which the engine also reads.
    include_countries   TEXT[] NOT NULL DEFAULT '{}',
    exclude_countries   TEXT[] NOT NULL DEFAULT '{}',
    include_languages   TEXT[] NOT NULL DEFAULT '{}',
    exclude_languages   TEXT[] NOT NULL DEFAULT '{}',
    demo_batch          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS campaigns_status_idx      ON campaigns (status);
CREATE INDEX IF NOT EXISTS campaigns_demo_batch_idx  ON campaigns (demo_batch);

CREATE TABLE IF NOT EXISTS creatives (
    id                BIGSERIAL PRIMARY KEY,
    campaign_id       BIGINT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    name              TEXT NOT NULL,
    ad_type           ad_type NOT NULL,
    width             INT NOT NULL CHECK (width  >= 0),   -- 0/0 == wildcard (creative_filters.h:51)
    height            INT NOT NULL CHECK (height >= 0),
    -- creative-level NARROWING only; never widens the campaign
    include_countries TEXT[] NOT NULL DEFAULT '{}',
    include_languages TEXT[] NOT NULL DEFAULT '{}',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS creatives_campaign_idx ON creatives (campaign_id);

CREATE TABLE IF NOT EXISTS budget_allocations (
    campaign_id      BIGINT  NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    cluster_id       INT     NOT NULL REFERENCES clusters(id)  ON DELETE RESTRICT,
    allocated_micros BIGINT  NOT NULL CHECK (allocated_micros >= 0),
    -- Durable ledger of confirmed spend. topup.lua seeds Redis from this on hash creation,
    -- which is what makes a Redis restart safe (finding B1).
    spent_micros     BIGINT  NOT NULL DEFAULT 0 CHECK (spent_micros >= 0),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (campaign_id, cluster_id)
);
CREATE INDEX IF NOT EXISTS budget_allocations_cluster_idx ON budget_allocations (cluster_id);
