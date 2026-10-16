# TechDeals ingestion spike

Turn heterogeneous hardware listings into a database that other agents can
query and tally for their analysis. Preserve the source, scraped item
and price facts, source-stated associations, and changes over time.

This fresh repository implements a bounded comparison of two shared LLM parser
candidates: conditional layout discovery followed by batch extraction and
validation, and a scoped evidence-reading tool loop. Both use the same typed
output, allowed evidence, and budgets. Accepted results must reach actual Neon
PostgreSQL and documented SQL reads. Parser agents and database consumer agents
have separate roles.

| Document | Purpose |
|---|---|
| [Problems and required outcomes](docs/problems.md) | The broader outcomes and limits of the initial exercise |
| [Data contract](docs/data-contract.md) | Evidence, output meanings, identity, dates, immutable interpretations, acceptance, history, and SQL consumption |
| [Implementation plan](docs/plan.md) | Frozen packages, two candidates, exact limits, implementation order, and requirements-to-checks trace |

Read them in that order. The design accepts unfamiliar sources through generic
JSON/HTML/XML/CSV/text readers, without a handwritten semantic parser for each
site. Each new or changed entry needing interpretation still needs the model to
read relevant evidence. Compatible unchanged content may reuse an accepted
interpretation while preserving a later observation. Unknown dates and identity
remain explicit.

The runtime uses Python 3.12, PydanticAI `slim[openrouter]`, typed Pydantic output,
Beautiful Soup generic readers, `psycopg`, `uv`, `pytest`, and `ruff`. The committed
model is `nvidia/nemotron-3.5-lightning:free` through
`https://openrouter.ai/api/v1`, with no alternative-model fallback. The exact
model must pass the structured multi-input and tools-plus-final capability
probes before compatibility is claimed. Reasoning is explicitly enabled. The free
Lightning route uses typed output tools, not native JSON-schema response mode. Supply secrets through Doppler-injected
process environment; the application does not load `.env` files.

Database work targets existing Neon project `late-wave-13685431`, a new child
branch `dev/ingestion-exercise` of main `br-restless-smoke-b4qofs8r`, and fresh
database `ingestion_spike`. Existing production schema is not the spike's
authority. No production changes are authorized by this exercise.

The initial full development attempts failed before producing usable rows. The
current first gate is deliberately smaller: **two complete native records,
11,584 bytes total, sent together in one extraction request**, followed by field
inspection and actual Neon reads. Discovery and tool loops are bypassed for this
gate. Six live SQL checks established the persistence behavior, while useful
extraction still requires its own source comparison.

From a clean checkout, run `uv sync --locked`. Inspect evidence without credentials:

```bash
uv run techdeals-ingest inspect --package small-a
uv run pytest -q
```

The normal test suite uses no model credentials. Live SQL checks require an explicit
`TEST_DATABASE_URL`; source regeneration requires the original pinned checkouts and
archive. Their skipped status is reported when those inputs are absent.

For the configured exercise target, the helper obtains the OpenRouter key and
Neon credentials from the existing Doppler projects and injects them into the child
process. It checks the branch-specific host/database and never writes credentials:

```bash
python scripts/with_doppler.py uv run techdeals-ingest migrate
python scripts/with_doppler.py uv run techdeals-ingest probe
python scripts/with_doppler.py uv run techdeals-ingest run \
  --package small-a --arm staged --direct --experiment my-small-proof \
  --output .runs/my-small-proof --fresh
```

The application itself reads `OPENROUTER_API_KEY`, `DATABASE_URL`, and
`DATABASE_URL_UNPOOLED` from its process environment. The helper's two Doppler
projects are `ai-automation/dev` and `apps-data/dev`; target metadata is in
`config/neon.toml`. You can inject those variables through an existing runner
instead. No `.env` loader is installed.

Use new output directories and explicit experiment names to preserve previous
trials. Omit `--fresh` to request compatible accepted-interpretation reuse. Reuse
currently requires matching native bytes and relevant metadata, parser/schema/
preparation code, dependency lock, and configuration. Changed unclassified source
fields cause a cache miss. The prototype does not claim to recognize arbitrary
collector-specific volatile fields.

`dev-b` selects the complete 24-product HTML capture. `holdout-c` is reserved for
the frozen comparison after development. Parser tools see only the selected
package; `corpus/oracle/expected.json` is evaluator input, never parser input.
Each trial retains typed output, request traces, usage, publication status, and SQL
read snapshots under its output directory. These files contain source material;
keep credential headers and environment dumps out of them.

Inspect a saved trial without model calls or database access:

```bash
uv run techdeals-ingest evaluate --package small-a \
  --trial .runs/my-small-proof --output .runs/my-small-proof-review.json
```

The evaluation shows exact assertions and explicit requirements needing source
review. It provides no overall correctness score. A JSON-schema pass, evidence
reference, or database round trip does not establish factual correctness.

All frozen captures currently have unknown trusted retrieval times. Their useful
results remain accessible through `listing_sightings`, `undated_evidence`, and
`unresolved_entries`. `current_listings`, `current_items`, and `current_prices`
require established source identities and retrieval times. `listing_changes`
keeps dated change episodes; `ingestion_failures` exposes failed/missing units.
Every view carries experiment and candidate (`arm`) dimensions.

For example, another agent can retrieve stored prices with their seller/location
facts and original date claims:

```sql
SELECT s.title, p.amount, p.currency, p.kind,
       s.fields->'seller'->'value' AS seller,
       s.fields->'location'->'value' AS location,
       s.observed_at, s.time_status, s.payload->'source_dates' AS source_dates
FROM listing_sightings s
JOIN prices p ON p.result_id = s.result_id AND p.offer_id = s.local_id
WHERE s.experiment_id = 'my-development' AND s.arm = 'staged';
```

Authored or derived fixtures do not establish missing real-source coverage. The
intended destination is a reviewed improvement to the main TechDeals project once
the parsing behavior and useful SQL results have been demonstrated.
