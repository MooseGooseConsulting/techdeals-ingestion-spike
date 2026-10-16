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
`nvidia/nemotron-3-super-120b-a12b:free` through OpenRouter, with reasoning enabled,
native JSON-schema output, at most one correction, and no alternative-model fallback. Model selection is
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
includes `result.json`, `report.json`, SDK messages and incremental reasoning/output events in `trace/`, and actual
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

## Small PydanticAI example

`scripts/extract_example.py` calls PydanticAI directly on one complete native
product, extracting title, SKU, price, currency and publication date. It uses the
free `nvidia/nemotron-3-super-120b-a12b:free` route with reasoning enabled,
independently of the full parser configuration:

```bash
PYDANTIC_AI_NO_BANNER=1 doppler run --project ai-automation --config dev -- \
  .venv/bin/python scripts/extract_example.py
```

Each run keeps its output and complete SDK conversation in a new directory under
`.runs/basic-product/`. Initial tool-output calls returned typed output in about
7–8 seconds, but repeats encoded absent currency as the string `"None"`. Inspection
of both model reasoning and raw HTTP showed correct reasoning about the missing
currency followed by a string in the returned tool arguments. Pydantic received
that string unchanged.

A native JSON-schema output call on the same source returned actual JSON `null`.
The example now uses that standard PydanticAI mode with its schema-prompt
template, so the model sees the field contract as well as being constrained by it. A nullable three-letter code
constraint catches malformed currency text without filling or guessing currency;
it does not prove that a syntactically valid code is supported by the source.
The configured model's native-output capability is explicit because the pinned
SDK's default profile does not recognize it; the capability was checked against
OpenRouter metadata and a real request.

A separate direct OpenAI SDK call to OpenRouter, without PydanticAI, returned
`KEY_OK` in 3.97 seconds using that same free model and the existing Doppler key.
This verifies authentication and a working completion route; it does not prove
full ingestion. The runtime now uses that working free route as well.

## Demonstration status

The OpenRouter key and free Nemotron Super route are verified by direct completion
and typed extraction calls. Native output returns real JSON nulls. Streaming
reasoning/output events and the final transcript are retained; outgoing request
JSON is captured before transmission without authentication headers.

The latest expanded single-product run returned the title, native and variant
IDs, SKU, original truncated description, availability and a `14.99` price with
null currency. Unsupported seller/location/condition remained null. It took two
model requests and 57.018 seconds, with reported cost zero. Five date occurrences
were extracted; the source contains eleven, so date coverage remains incomplete.
Multi-product extraction and the updated parser-to-SQL path have not been
demonstrated. This is a work-in-progress handoff, not a completed or approved
architecture. The owner has stopped further implementation pending design review.

Local artifacts from that run are under `.runs/expanded-spd-checked/` (result,
request JSON, streamed events, and final transcript). They remain ignored runtime
artifacts. The corpus is committed and unchanged.

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
