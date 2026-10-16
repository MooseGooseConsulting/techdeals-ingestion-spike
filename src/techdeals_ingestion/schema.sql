CREATE TABLE IF NOT EXISTS raw_content (sha256 text PRIMARY KEY, content bytea NOT NULL);
CREATE TABLE IF NOT EXISTS captures (
 capture_id text PRIMARY KEY, acquisition_key text NOT NULL, sha256 text NOT NULL REFERENCES raw_content,
 media_type text NOT NULL, source_namespace text, retrieved_at timestamptz, linked_group text,
 metadata jsonb NOT NULL, UNIQUE(acquisition_key,capture_id));
CREATE TABLE IF NOT EXISTS attempts (
 attempt_key text PRIMARY KEY, package_id text NOT NULL, experiment_id text NOT NULL, arm text NOT NULL,
 result jsonb NOT NULL, errors jsonb NOT NULL DEFAULT '[]', created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS observations (
 observation_id text PRIMARY KEY, acquisition_keys jsonb NOT NULL, entry_refs jsonb NOT NULL,
 observed_at timestamptz, time_status text NOT NULL);
CREATE TABLE IF NOT EXISTS interpretations (
 result_id text PRIMARY KEY, observation_id text NOT NULL REFERENCES observations,
 attempt_key text NOT NULL REFERENCES attempts, experiment_id text NOT NULL, arm text NOT NULL,
 unit_id text NOT NULL, semantic_key text NOT NULL, parser_fingerprint text NOT NULL,
 payload jsonb NOT NULL, input_capture_ids jsonb NOT NULL, reused_from text REFERENCES interpretations,
 UNIQUE(result_id,observation_id));
CREATE TABLE IF NOT EXISTS accepted (
 observation_id text NOT NULL, experiment_id text NOT NULL, arm text NOT NULL, result_id text NOT NULL,
 PRIMARY KEY(observation_id,experiment_id,arm),
 FOREIGN KEY(result_id,observation_id) REFERENCES interpretations(result_id,observation_id));
CREATE TABLE IF NOT EXISTS listings (
 listing_id text PRIMARY KEY, source_namespace text NOT NULL, native_id text NOT NULL, variant_key text NOT NULL,
 UNIQUE(source_namespace,native_id,variant_key));
CREATE TABLE IF NOT EXISTS offers (
 result_id text NOT NULL REFERENCES interpretations, local_id text NOT NULL, listing_id text REFERENCES listings,
 title text, fields jsonb NOT NULL, payload jsonb NOT NULL, semantic_payload jsonb NOT NULL, PRIMARY KEY(result_id,local_id));
CREATE TABLE IF NOT EXISTS items (
 result_id text NOT NULL, local_id text NOT NULL, offer_id text NOT NULL, title text, role text NOT NULL,
 quantity numeric, attributes jsonb NOT NULL, payload jsonb NOT NULL, PRIMARY KEY(result_id,local_id),
 FOREIGN KEY(result_id,offer_id) REFERENCES offers(result_id,local_id));
CREATE TABLE IF NOT EXISTS prices (
 result_id text NOT NULL, local_id text NOT NULL, offer_id text NOT NULL, amount numeric, currency text,
 kind text NOT NULL, quantity numeric, scope_refs jsonb NOT NULL, payload jsonb NOT NULL,
 PRIMARY KEY(result_id,local_id), FOREIGN KEY(result_id,offer_id) REFERENCES offers(result_id,local_id));
CREATE TABLE IF NOT EXISTS relations (
 result_id text NOT NULL, offer_id text NOT NULL, ordinal integer NOT NULL, payload jsonb NOT NULL,
 PRIMARY KEY(result_id,offer_id,ordinal), FOREIGN KEY(result_id,offer_id) REFERENCES offers(result_id,local_id));
-- Additive, repeatable strengthening for databases initialized by the first spike migration.
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='captures_acquisition_unique') THEN
  ALTER TABLE captures ADD CONSTRAINT captures_acquisition_unique UNIQUE(acquisition_key);
 END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='interpretations_selection_unique') THEN
  ALTER TABLE interpretations ADD CONSTRAINT interpretations_selection_unique
   UNIQUE(result_id,observation_id,experiment_id,arm);
 END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='accepted_selection_fk') THEN
  ALTER TABLE accepted ADD CONSTRAINT accepted_selection_fk
   FOREIGN KEY(result_id,observation_id,experiment_id,arm)
   REFERENCES interpretations(result_id,observation_id,experiment_id,arm);
 END IF;
END $$;
CREATE OR REPLACE VIEW ingestion_failures AS
 SELECT a.attempt_key,a.package_id,a.experiment_id,a.arm,a.created_at,
        e.value->>'unit_id' AS unit_id,e.value->>'error' AS error,a.result AS attempt_payload
 FROM attempts a CROSS JOIN LATERAL jsonb_array_elements(a.errors) e(value);
-- Accepted unit outcomes remain queryable even when no offer row was produced.
CREATE OR REPLACE VIEW ingestion_outcomes AS
 SELECT i.result_id,i.observation_id,i.experiment_id,i.arm,i.unit_id,i.input_capture_ids,
 i.payload->>'status' AS status,i.payload AS unit_payload,ob.observed_at,ob.time_status,i.reused_from
 FROM interpretations i JOIN accepted a ON a.result_id=i.result_id
 JOIN observations ob ON ob.observation_id=i.observation_id;
CREATE OR REPLACE VIEW listing_sightings AS
 SELECT o.*, i.observation_id,i.experiment_id,i.arm,i.unit_id,i.input_capture_ids,
 ob.observed_at,ob.time_status,i.payload AS unit_payload,i.reused_from
 FROM offers o JOIN interpretations i USING(result_id)
 JOIN accepted a ON a.result_id=i.result_id JOIN observations ob ON ob.observation_id=i.observation_id;
CREATE OR REPLACE VIEW unresolved_entries AS SELECT * FROM listing_sightings WHERE listing_id IS NULL;
CREATE OR REPLACE VIEW undated_evidence AS SELECT * FROM listing_sightings WHERE observed_at IS NULL;
-- Partial sightings retain their own evidence and dates. Only complete states can
-- replace current state; no fields or freshness dates are merged across observations.
CREATE OR REPLACE VIEW current_listings AS
 WITH complete AS (
 SELECT * FROM listing_sightings WHERE listing_id IS NOT NULL AND observed_at IS NOT NULL
 AND unit_payload->>'status'='complete'),
 ranked AS (SELECT s.*, dense_rank() OVER (
 PARTITION BY listing_id,experiment_id,arm ORDER BY observed_at DESC) AS time_rank
 FROM complete s),
 representatives AS (
 SELECT DISTINCT ON (listing_id,experiment_id,arm,semantic_payload) * FROM ranked
 WHERE time_rank=1
 ORDER BY listing_id,experiment_id,arm,semantic_payload,result_id,local_id)
 SELECT r.*, (SELECT count(DISTINCT s.semantic_payload)>1 FROM complete s
 WHERE s.listing_id=r.listing_id AND s.experiment_id=r.experiment_id AND s.arm=r.arm
 AND s.observed_at=r.observed_at) AS equal_time_conflict
 FROM representatives r;
CREATE OR REPLACE VIEW current_items AS
 SELECT i.*, c.listing_id,c.observation_id,c.experiment_id,c.arm,c.observed_at,c.equal_time_conflict
 FROM items i JOIN current_listings c ON c.result_id=i.result_id AND c.local_id=i.offer_id;
CREATE OR REPLACE VIEW current_prices AS
 SELECT p.*, c.listing_id,c.observation_id,c.experiment_id,c.arm,c.observed_at,c.equal_time_conflict
 FROM prices p JOIN current_listings c ON c.result_id=p.result_id AND c.local_id=p.offer_id;
CREATE OR REPLACE VIEW listing_changes AS
 WITH grouped AS (
 SELECT listing_id,experiment_id,arm,observed_at,count(DISTINCT semantic_payload)>1 AS conflict,
        (jsonb_agg(DISTINCT semantic_payload)->0) AS meaning
 FROM listing_sightings WHERE listing_id IS NOT NULL AND observed_at IS NOT NULL
 AND unit_payload->>'status'='complete'
 GROUP BY listing_id,experiment_id,arm,observed_at),
 ordered AS (SELECT *,lag(meaning) OVER
 (PARTITION BY listing_id,experiment_id,arm ORDER BY observed_at) AS previous_payload
 FROM grouped WHERE NOT conflict)
 SELECT s.*,o.previous_payload FROM ordered o JOIN listing_sightings s
 USING(listing_id,experiment_id,arm,observed_at)
 WHERE s.unit_payload->>'status'='complete'
 AND (o.previous_payload IS NULL OR o.previous_payload IS DISTINCT FROM o.meaning);
