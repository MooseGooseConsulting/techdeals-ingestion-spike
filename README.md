# TechDeals ingestion spike

Turn heterogeneous hardware listings into a database that other agents can
query, relate, and tally for their analysis. Preserve the source, what a purchase
includes, what its price covers, and what changes over time.

This is a fresh implementation repository. The owner requested it after the
previous work failed to demonstrate the useful application they expected.
Existing TechDeals implementations are not the starting architecture here.

Start with the [problems and required outcomes](docs/problems.md), then read the
[implementation plan](docs/plan.md). Its proposed first milestone
is one working path from varied real captures through batched free-model
interpretation and database storage to information other agents can query.

The intended destination is an improvement to the main TechDeals project after
the behavior has been demonstrated. Organization ownership or a successful
model request does not establish that result.

Current state: initial plan only; implementation has not started.
