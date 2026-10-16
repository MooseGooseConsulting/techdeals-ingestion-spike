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
    PRIMARY KEY(entry_id, ordinal)
);

CREATE TABLE IF NOT EXISTS ingest_entry_sources (
    entry_id text NOT NULL REFERENCES ingest_entry_rows(entry_id),
    input_id text NOT NULL REFERENCES ingest_sources(input_id),
    locator text,
    PRIMARY KEY(entry_id, input_id, locator)
);

CREATE VIEW ingest_entries AS
SELECT e.*, a.parser_key, a.reuse_of, a.created_at AS imported_at,
       COALESCE(src.input_ids, '[]'::jsonb) AS input_ids,
       src.namespace
FROM ingest_entry_rows e
JOIN ingest_attempts a USING (attempt_id)
LEFT JOIN LATERAL (
    SELECT jsonb_agg(DISTINCT s.input_id ORDER BY s.input_id) AS input_ids,
           jsonb_agg(jsonb_build_object('input_id',s.input_id,'locator',NULLIF(es.locator,''))
                     ORDER BY s.input_id, es.locator) AS source_refs,
           CASE WHEN count(DISTINCT s.namespace) = 1 THEN min(s.namespace) END AS namespace
    FROM ingest_entry_sources es JOIN ingest_sources s USING (input_id)
    WHERE es.entry_id = e.entry_id
) src ON true;

CREATE VIEW ingest_prices AS
SELECT p.*, e.attempt_id, e.title, e.native_id, e.variant_id, e.sku, e.url,
       e.seller, e.location, e.condition, e.availability, e.attributes,
       e.source_dates, e.issues, e.identity_key, e.observed_at, e.has_reported_issues,
       e.parser_key, e.reuse_of, e.imported_at, e.input_ids, e.namespace
FROM ingest_price_rows p JOIN ingest_entries e USING (entry_id);

CREATE VIEW ingest_history AS
SELECT e.*,
       COALESCE((SELECT jsonb_agg(to_jsonb(p) ORDER BY p.ordinal)
                 FROM ingest_prices p WHERE p.entry_id=e.entry_id), '[]'::jsonb) AS prices
FROM ingest_entries e;

CREATE VIEW ingest_current_entries AS
WITH dated AS (
    SELECT * FROM ingest_entries
    WHERE identity_key IS NOT NULL AND observed_at IS NOT NULL AND NOT has_reported_issues
), latest AS (
    SELECT d.*, dense_rank() OVER (PARTITION BY identity_key ORDER BY observed_at DESC) AS date_rank
    FROM dated d
), latest_states AS (
    SELECT DISTINCT ON (identity_key, observed_at, semantic_hash) *
    FROM latest WHERE date_rank=1
    ORDER BY identity_key, observed_at, semantic_hash, entry_id
), conflicts AS (
    SELECT identity_key, observed_at, count(DISTINCT semantic_hash)>1 AS equal_time_conflict
    FROM latest WHERE date_rank=1 GROUP BY identity_key, observed_at
)
SELECT s.*, c.equal_time_conflict
FROM latest_states s JOIN conflicts c USING(identity_key, observed_at);

CREATE VIEW ingest_current_prices AS
SELECT p.*, c.equal_time_conflict
FROM ingest_prices p JOIN ingest_current_entries c USING(entry_id);

CREATE VIEW ingest_failures AS
SELECT attempt_id, receipt_key, semantic_key, parser_key, source_ids,
       reuse_of, error, created_at, source_ids AS input_ids
FROM ingest_attempts WHERE error IS NOT NULL;
