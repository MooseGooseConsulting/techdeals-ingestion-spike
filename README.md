# TechDeals ingestion spike

Turn heterogeneous hardware listings into a database that other agents can
query, relate, and tally for their analysis. Preserve the source, what a purchase
includes, what its price covers, and what changes over time.

Inputs come from multiple scrapers, APIs, feeds, browser collectors, and exports.
Bright Data is one collection option among them.

This is a fresh implementation repository. The owner requested it after the
previous work failed to demonstrate the useful application they expected.
Existing TechDeals implementations are not the starting architecture here.

The project documents have separate responsibilities:

| Document | Purpose |
|---|---|
| [Problems and required outcomes](docs/problems.md) | What needs solving and what the resulting database must support |
| [Data contract](docs/data-contract.md) | Scraped fields, transformation into records, dates, parsing, identity, deduplication, and scraper output |
| [Implementation plan](docs/plan.md) | The proposed work order and how each problem will be addressed |

Read them in that order. The proposed first delivery is one working path from
varied real captures through interpretation and storage to information other
agents can query, including dates and correct rescrape handling.
It starts with [several different input shapes](docs/data-contract.md#different-input-shapes-through-the-same-path)
together, including payloads containing many entries and several payloads
describing one listing. The parser and database representation must be checked
against that variety before implementation.
The central proposal is one shared LLM-backed parser that also works on
unfamiliar sources, supported by general format readers and database code.

The intended destination is an improvement to the main TechDeals project after
the behavior has been demonstrated. Organization ownership or a successful
model request does not establish that result.

Current state: draft requirements and design documents; implementation has not started.
