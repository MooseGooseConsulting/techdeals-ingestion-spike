# TechDeals ingestion spike

Turn heterogeneous hardware listings into a database that other agents can
query and tally for their analysis. Preserve the source, scraped item
and price facts, source-stated associations, and changes over time.

The runtime has one path: **native sources → one PydanticAI extraction agent →
entries with named fields and nested prices → Neon PostgreSQL → SQL reads**.
The former graph contract, two parser strategies, discovery loop and experiment
framework have been removed. The corpus and its provenance stay.

| Document | Purpose |
|---|---|
| [Design](docs/design.md) | Entry interface and application responsibilities |
| [Problems](docs/problems.md) | Required outcomes and real-source gaps |
| [Data contract](docs/data-contract.md) | Source preservation, dates, identity, repeat scrapes and SQL meanings |
| [Plan](docs/plan.md) | Implementation sequence and acceptance demonstrations |

The stack is Python 3.12, PydanticAI, Pydantic, psycopg, PostgreSQL, uv, pytest
and Ruff. Beautiful Soup checks optional HTML locators. The committed model is
`nvidia/nemotron-3.5-lightning:free` through OpenRouter, with reasoning enabled,
at most one correction, and no alternative-model fallback. Model selection is
in `config/parser.toml`. Credentials enter through Doppler-injected process
environment; the application does not load `.env` files.

## Run it

```bash
uv sync --locked
uv run techdeals-ingest inspect --package small-a
uv run pytest -q
python scripts/with_doppler.py uv run techdeals-ingest migrate
python scripts/with_doppler.py uv run techdeals-ingest ingest \
  --package small-a --output .runs/my-small-proof --fresh
python scripts/with_doppler.py uv run techdeals-ingest query prices
```

Use a new output directory for each run. Arbitrary native files use the same
parser; supply trusted acquisition context when known:

```bash
python scripts/with_doppler.py uv run techdeals-ingest ingest \
  --input /path/to/scraped.json --namespace source-site \
  --retrieved-at 2026-10-16T12:00:00Z --acquisition-key collector-run-record \
  --output .runs/native-import
```

Omit retrieval time when unknown. File modification time is never promoted to
market observation time. The command retains bytes before inference. Output
includes `result.json`, `report.json`, SDK messages in `trace/`, and actual
attempt-scoped `database-entries.json` and `database-prices.json`. A schema pass
or an empty issues list does not establish factual correctness. Reports with
issues return a nonzero exit status while retaining useful extracted entries.
Oversized inputs and unsupported text encodings are explicit failures/issues,
not silently shortened source material.

The helper reads the existing Doppler projects `ai-automation/dev` and
`apps-data/dev`, checks the host/database against `config/neon.toml`, and injects
credentials without writing them. The target is the existing isolated Neon
branch `dev/ingestion-exercise` (`br-curly-dust-b4dszjr9`), project
`late-wave-13685431`, database `ingestion_spike`. No production schema or
collector is changed. Local traces contain source material and remain ignored.

## Read stored data

Entry fields are named columns, prices use exact PostgreSQL NUMERIC, and variable
attributes and native date claims use JSONB. SQL views are `ingest_entries`,
`ingest_prices`, `ingest_current_entries`, `ingest_current_prices`,
`ingest_history`, and `ingest_failures`. The CLI accepts those names without the
`ingest_` prefix and supports `--attempt-id` for a particular run.

```sql
SELECT title, amount, currency, original, kind, basis, conditions,
       seller, location, observed_at, source_dates, input_ids
FROM ingest_prices
WHERE attempt_id = 'the-attempt-id-from-report';
```

`entries` and `prices` expose stored interpretations, including unknown-time or
unresolved-identity evidence. They include sightings and should not be counted
as independent offers. Current views group by namespace, native ID, variant and
seller, require trusted retrieval time and no reported issues, and expose
conflicting states at an equal latest time. Unknown identity stays unresolved.
History retains sightings; later unchanged acquisitions can reuse the extraction
without another model call. Reimporting the same capture/result is idempotent.
Revised interpretations and identity reliability still require inspection;
these keys do not prove arbitrary source identity semantics.

## Demonstration status

The first replacement-runtime `small-a` run (two complete native captures,
11,584 bytes) timed out at the configured 90-second limit before the model
returned extraction output. Neon retained both inputs and the failed attempt;
zero entries were produced in that run. The subsequent single-record call
(2,494 native bytes) also timed out at 90 seconds without output. An explicit
parser-only trial of the free `poolside/laguna-xs-2.1:free` route returned HTTP
429. That trial did not change the committed Lightning configuration. Useful
live extraction is still unproven; storage tests do not substitute for it. Offline and explicit live SQL tests check
application mechanics; they do not certify model extraction quality.

All frozen captures currently have unknown trusted retrieval times. Do not
label them current market observations. The full 24-product HTML capture is
available as `dev-b`; `holdout-c` remains reserved. The oracle is review input,
never parser input. Real multi-item sale posts, split-auction captures, verified
retrieval-date semantics and acquisition provenance for additional collector
routes remain gaps. Large-document preparation and throughput are subsequent
demonstrations, not delivered claims.

The destination is a reviewed improvement to the main TechDeals project after
useful extraction and repeat-scrape behavior have been demonstrated. This PR
stays open for review; it does not authorize production incorporation.
