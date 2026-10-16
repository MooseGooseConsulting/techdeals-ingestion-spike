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
CREATE OR REPLACE VIEW listing_sightings AS
 SELECT o.*, i.observation_id,i.experiment_id,i.arm,i.unit_id,i.input_capture_ids,
 ob.observed_at,ob.time_status,i.payload AS unit_payload,i.reused_from
 FROM offers o JOIN interpretations i USING(result_id)
 JOIN accepted a ON a.result_id=i.result_id JOIN observations ob ON ob.observation_id=i.observation_id;
CREATE OR REPLACE VIEW unresolved_entries AS SELECT * FROM listing_sightings WHERE listing_id IS NULL;
CREATE OR REPLACE VIEW undated_evidence AS SELECT * FROM listing_sightings WHERE observed_at IS NULL;
CREATE OR REPLACE VIEW current_listings AS
 WITH ranked AS (SELECT s.*, dense_rank() OVER (
 PARTITION BY listing_id,experiment_id,arm ORDER BY observed_at DESC) AS time_rank
 FROM listing_sightings s WHERE listing_id IS NOT NULL AND observed_at IS NOT NULL)
 SELECT *, count(*) OVER (PARTITION BY listing_id,experiment_id,arm) > 1 AS equal_time_conflict
 FROM ranked WHERE time_rank=1;
CREATE OR REPLACE VIEW current_items AS
 SELECT i.*, c.listing_id,c.observation_id,c.experiment_id,c.arm,c.observed_at,c.equal_time_conflict
 FROM items i JOIN current_listings c ON c.result_id=i.result_id AND c.local_id=i.offer_id;
CREATE OR REPLACE VIEW current_prices AS
 SELECT p.*, c.listing_id,c.observation_id,c.experiment_id,c.arm,c.observed_at,c.equal_time_conflict
 FROM prices p JOIN current_listings c ON c.result_id=p.result_id AND c.local_id=p.offer_id;
CREATE OR REPLACE VIEW listing_changes AS
 WITH ordered AS (SELECT s.*,lag(semantic_payload) OVER
 (PARTITION BY listing_id,experiment_id,arm ORDER BY observed_at,observation_id) AS previous_payload
 FROM listing_sightings s WHERE listing_id IS NOT NULL AND observed_at IS NOT NULL)
 SELECT * FROM ordered WHERE previous_payload IS NULL OR
 previous_payload IS DISTINCT FROM semantic_payload;
