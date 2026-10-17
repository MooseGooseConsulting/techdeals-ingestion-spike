-- These are derived query surfaces owned by this small ingestion schema.
-- Recreate them in dependency order so additive column changes remain repeatable.
DROP VIEW IF EXISTS ingest_current_prices;
DROP VIEW IF EXISTS ingest_current_entries;
DROP VIEW IF EXISTS ingest_history;
DROP VIEW IF EXISTS ingest_prices;
DROP VIEW IF EXISTS ingest_entries;
DROP VIEW IF EXISTS ingest_failures;

CREATE TABLE IF NOT EXISTS ingest_raw_content (
    sha256 text PRIMARY KEY,
    content bytea NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_sources (
    input_id text PRIMARY KEY,
    acquisition_key text NOT NULL UNIQUE,
    sha256 text NOT NULL REFERENCES ingest_raw_content(sha256),
    media_type text NOT NULL,
    namespace text,
    retrieved_at timestamptz,
    url text,
    linked_group text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS ingest_attempts (
    attempt_id text PRIMARY KEY,
    receipt_key text NOT NULL UNIQUE,
    semantic_key text NOT NULL,
    parser_key text NOT NULL,
    source_ids jsonb NOT NULL,
    reuse_of text REFERENCES ingest_attempts(attempt_id),
    result jsonb,
    error text,
    saved_entries integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((result IS NULL) <> (error IS NULL))
);
CREATE INDEX IF NOT EXISTS ingest_attempts_cache_idx
    ON ingest_attempts(semantic_key, parser_key, created_at DESC)
    WHERE error IS NULL AND result IS NOT NULL;

CREATE TABLE IF NOT EXISTS ingest_entry_rows (
    entry_id text PRIMARY KEY,
    attempt_id text NOT NULL REFERENCES ingest_attempts(attempt_id),
    title text,
    description text,
    native_id text,
    variant_id text,
    sku text,
    url text,
    seller text,
    location text,
    condition text,
    availability text,
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    source_dates jsonb NOT NULL DEFAULT '[]'::jsonb,
    issues jsonb NOT NULL DEFAULT '[]'::jsonb,
    identity_key text,
    semantic_hash text NOT NULL,
    observed_at timestamptz,
    has_reported_issues boolean NOT NULL
);
ALTER TABLE ingest_entry_rows
    ADD COLUMN IF NOT EXISTS has_reported_issues boolean;
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='ingest_entry_rows' AND column_name='is_complete'
    ) THEN
        UPDATE ingest_entry_rows
        SET has_reported_issues = NOT is_complete
        WHERE has_reported_issues IS NULL;
    ELSE
        UPDATE ingest_entry_rows SET has_reported_issues = false
        WHERE has_reported_issues IS NULL;
    END IF;
END $$;
DROP INDEX IF EXISTS ingest_entries_identity_idx;
ALTER TABLE ingest_entry_rows DROP COLUMN IF EXISTS is_complete;
ALTER TABLE ingest_entry_rows ALTER COLUMN has_reported_issues SET DEFAULT false;
ALTER TABLE ingest_entry_rows ALTER COLUMN has_reported_issues SET NOT NULL;
COMMENT ON COLUMN ingest_entry_rows.has_reported_issues IS
    'True when the extraction explicitly reported a source or entry issue; false does not assert factual completeness.';
CREATE INDEX IF NOT EXISTS ingest_entries_identity_idx
    ON ingest_entry_rows(identity_key, observed_at DESC)
    WHERE identity_key IS NOT NULL AND observed_at IS NOT NULL AND NOT has_reported_issues;

CREATE TABLE IF NOT EXISTS ingest_price_rows (
    entry_id text NOT NULL REFERENCES ingest_entry_rows(entry_id),
    ordinal integer NOT NULL,
    amount numeric,
    currency text,
    original text NOT NULL,
    kind text NOT NULL,
    basis text,
    conditions text,
    source_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY(entry_id, ordinal)
);
ALTER TABLE ingest_price_rows
    ADD COLUMN IF NOT EXISTS source_refs jsonb NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE IF NOT EXISTS ingest_entry_sources (
    entry_id text NOT NULL REFERENCES ingest_entry_rows(entry_id),
    input_id text NOT NULL REFERENCES ingest_sources(input_id),
    locator text,
    PRIMARY KEY(entry_id, input_id, locator)
);

CREATE VIEW ingest_entries AS
SELECT e.*, a.parser_key, a.reuse_of, a.created_at AS imported_at,
       COALESCE(src.input_ids, '[]'::jsonb) AS input_ids,
       COALESCE(src.source_refs, '[]'::jsonb) AS source_refs,
       COALESCE(src.acquisition_keys, '[]'::jsonb) AS acquisition_keys,
       src.namespace
FROM ingest_entry_rows e
JOIN ingest_attempts a USING (attempt_id)
LEFT JOIN LATERAL (
    SELECT jsonb_agg(DISTINCT s.input_id ORDER BY s.input_id) AS input_ids,
           jsonb_agg(jsonb_build_object('input_id',s.input_id,'locator',NULLIF(es.locator,''))
                     ORDER BY s.input_id, es.locator) AS source_refs,
           jsonb_agg(DISTINCT s.acquisition_key ORDER BY s.acquisition_key) AS acquisition_keys,
           CASE WHEN count(DISTINCT s.namespace) = 1 THEN min(s.namespace) END AS namespace
    FROM ingest_entry_sources es JOIN ingest_sources s USING (input_id)
    WHERE es.entry_id = e.entry_id
) src ON true;

CREATE VIEW ingest_prices AS
SELECT p.*, e.attempt_id, e.title, e.native_id, e.variant_id, e.sku, e.url,
       e.seller, e.location, e.condition, e.availability, e.attributes,
       e.source_dates, e.issues, e.identity_key, e.observed_at, e.has_reported_issues,
       e.parser_key, e.reuse_of, e.imported_at, e.input_ids, e.source_refs AS entry_source_refs,
       e.acquisition_keys, e.namespace
FROM ingest_price_rows p JOIN ingest_entries e USING (entry_id);

CREATE VIEW ingest_history AS
SELECT e.*,
       COALESCE((SELECT jsonb_agg(to_jsonb(p) ORDER BY p.ordinal)
                 FROM ingest_prices p WHERE p.entry_id=e.entry_id), '[]'::jsonb) AS prices
FROM ingest_entries e;

CREATE VIEW ingest_current_entries AS
WITH eligible AS (
    SELECT * FROM ingest_entries
    WHERE observed_at IS NOT NULL
      AND NOT has_reported_issues
      AND identity_key IS NOT NULL
), identity_latest AS (
    -- A later partial attempt replaces only the identities it successfully stored.
    SELECT acquisition_keys, identity_key, max(imported_at) AS imported_at
    FROM eligible
    GROUP BY acquisition_keys, identity_key
), kept AS (
    SELECT e.*
    FROM eligible e
    JOIN identity_latest l
      ON e.acquisition_keys = l.acquisition_keys
     AND e.identity_key = l.identity_key
     AND e.imported_at = l.imported_at
), facts AS (
    SELECT k.*,
           md5((jsonb_build_object(
             'title', k.title,
             'description', k.description,
             'native_id', k.native_id,
             'variant_id', k.variant_id,
             'sku', k.sku,
             'url', k.url,
             'seller', k.seller,
             'location', k.location,
             'condition', k.condition,
             'availability', k.availability,
             'attributes', k.attributes,
             'issues', k.issues,
             'source_dates', COALESCE((
               SELECT jsonb_agg((d - 'source_refs') ORDER BY ordinality)
               FROM jsonb_array_elements(k.source_dates) WITH ORDINALITY AS dated(d, ordinality)
             ), '[]'::jsonb),
             'prices', COALESCE((
               SELECT jsonb_agg(jsonb_build_object(
                 'ordinal', p.ordinal,
                 'amount', p.amount,
                 'currency', p.currency,
                 'original', p.original,
                 'kind', p.kind,
                 'basis', p.basis,
                 'conditions', p.conditions
               ) ORDER BY p.ordinal)
               FROM ingest_price_rows p
               WHERE p.entry_id = k.entry_id
             ), '[]'::jsonb)
           ))::text) AS fact_key
    FROM kept k
), latest_observed AS (
    SELECT identity_key, max(observed_at) AS observed_at
    FROM facts
    GROUP BY identity_key
), conflicts AS (
    SELECT f.identity_key, f.observed_at,
           count(DISTINCT f.fact_key) > 1
           AND count(DISTINCT f.acquisition_keys) > 1 AS equal_time_conflict
    FROM facts f
    JOIN latest_observed l USING (identity_key, observed_at)
    GROUP BY f.identity_key, f.observed_at
), chosen AS (
    SELECT DISTINCT ON (f.identity_key, f.fact_key) f.entry_id, f.identity_key, f.observed_at
    FROM facts f
    JOIN latest_observed l USING (identity_key, observed_at)
    ORDER BY f.identity_key, f.fact_key, f.imported_at DESC, f.entry_id
)
SELECT e.*, COALESCE(c.equal_time_conflict, false) AS equal_time_conflict
FROM chosen
JOIN ingest_entries e USING (entry_id)
LEFT JOIN conflicts c USING (identity_key, observed_at);

CREATE VIEW ingest_current_prices AS
SELECT p.*, c.equal_time_conflict
FROM ingest_prices p JOIN ingest_current_entries c USING(entry_id);

CREATE VIEW ingest_failures AS
SELECT attempt_id, receipt_key, semantic_key, parser_key, source_ids,
       reuse_of, error, created_at, source_ids AS input_ids
FROM ingest_attempts WHERE error IS NOT NULL;
