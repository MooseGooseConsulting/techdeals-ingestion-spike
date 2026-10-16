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
model is `nvidia/nemotron-3-super-120b-a12b:free` through
`https://openrouter.ai/api/v1`, with no alternative-model fallback. The exact
model must pass the structured multi-input and tools-plus-final capability
probes before compatibility is claimed. Supply secrets through Doppler-injected
process environment; the application does not load `.env` files.

Database work targets existing Neon project `late-wave-13685431`, a new child
branch `dev/ingestion-exercise` of main `br-restless-smoke-b4qofs8r`, and fresh
database `ingestion_spike`. Existing production schema is not the spike's
authority. No production changes are authorized by this exercise.

Current state: approved design under implementation. Run commands and exact
SQL object names will be documented with the integrated executable; this README
does not yet claim a successful model run, created database, or live SQL result.
The deliverable is a frozen paired report and inspectable database evidence,
including failures and unattempted cases. Authored or derived fixtures do not
establish missing real-source coverage. The intended destination is a reviewed,
demonstrated improvement to the main TechDeals project after this behavior has
been established.
