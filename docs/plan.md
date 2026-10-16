# From captured listings to agent-queryable information

Status: initial proposal, 2026-10-16. No implementation or database has been
created for this spike.

## Problems and required outcomes

Read [Problems this project must solve](problems.md) first. It states the
problems and required behavior in their own terms. This document proposes the
implementation approach; completing a step does not by itself solve a problem.

Other agents query the database and use the information in their own tasks.
The first delivery is an ingestion pipeline and queryable database with
documented data meanings. Its consumer requirements are composable retrieval,
relationships, quantities, price scope, provenance, uncertainty, and history.
The owner's personal use of a query command or buying interface is not a
requirement.

| Problem | Where this plan addresses it | What must be demonstrated |
|---|---|---|
| [P1: heterogeneous information](problems.md#p1-heterogeneous-information) | First deliverable, steps 1–4; broader information extension | Different real purchase structures become useful query results without losing their context |
| [P2: contents, quantities, and purchase structure](problems.md#p2-contents-quantities-and-purchase-structure) | First deliverable, steps 1–5 | Data retrieved by agents preserves contents, quantities, installed/optional/absent facts, and what can be bought separately |
| [P3: price scope and cost apportionment](problems.md#p3-what-prices-cover-and-how-costs-are-apportioned) | First deliverable for stated purchase prices; cost apportionment extension for attributed costs | Amounts cover the correct purchases; justified allocations remain separately identified and traceable |
| [P4: meaningful matching and comparison](problems.md#p4-meaningful-matching-and-comparison) | First deliverable, steps 2–5; broader information extension | Queries return suitable purchases and exclude misleading near matches using the relevant configuration facts |
| [P5: source evidence, dates, and history](problems.md#p5-source-evidence-dates-and-history) | First deliverable for source traceability; continuity extension for repeated captures and interpretation changes | Agent query results retain source/time context and history without invented events, duplicated purchases, or moved prices |
| [P6: useful incomplete information and visible failures](problems.md#p6-useful-incomplete-information-and-visible-failures) | First deliverable, steps 1–5; retry/interruption work in the continuity extension | Partial evidence remains retrievable, with explicit unknowns, conflicts, failures, and exclusions |
| [P7: developing categories](problems.md#p7-categories-that-develop-with-the-information) | Broader information and categories extension | New meaningful categories can be represented and queried with their relevant attributes and relationships |

All seven problems remain open. The sequence below is proposed; the selection
of examples and the size of the first slice have not been settled with the owner.

## First deliverable: one useful, executable slice

1. **Choose varied real captures and inspect them.** Candidate structures include
   a configured server, a standalone component or homogeneous lot, and a
   multi-item sale posting. Choose examples for the problems they expose;
   there is no fixed capture count. Use original payloads and source context, not a previous
   parser's output or reference spreadsheet as the expected truth. Check that
   they exercise different purchase structures. Record the exact source
   excerpts needed to explain the expected answers alongside those examples.
   Preserve missing dates and contradictions explicitly.

   Done when each capture has a short, independently inspected description of
   what is offered, its contents/counts, the stated amounts, and unknowns.

2. **Define the information consuming agents need to retrieve.** Identify the
   facts, relationships, quantities, purchase configurations, price scope, source
   references, and unknowns the inspected captures require. Preserve distinctions
   such as installed versus supported hardware, per-component versus aggregate
   capacity, and a bundled component versus a separately purchasable item.
   Define their meanings and how agents can retrieve and combine them for their
   own tasks. Link these requirements to the problems above.

   Done when expected stored facts and relationships are explicit, including
   unknowns and conflicts, and representative agent queries can establish whether
   those meanings survive. Example queries demonstrate access; they do not limit
   the database to a fixed list of questions.

3. **Choose the smallest representation and tools for that slice.** Sketch the
   required records and relationships against the examples, then implement
   them. Do not begin with the previous tables or recreate its module tree.
   Use PostgreSQL on an isolated branch of an existing Neon project. Python
   and Instructor are proposed starting tools for batched extraction; first
   check compatibility with the selected free endpoint and its output mode.
   Keep this choice explicit in the implementation PR. Add dependencies only
   for work this slice actually needs. A TimescaleDB extension remains a
   separate workload decision; time-series requirements do not depend on it.

   Done when every stored field has a concrete purpose in agent consumption or in
   retaining their source evidence, and the chosen model request works in a batch.

4. **Build and run the complete path.** A documented ingestion entry point takes
   the selected captures, interprets entries or fields needing contextual
   interpretation in batched free-model requests, and stores the evidence and
   interpreted information in the database queried by other agents. Preserve
   usable structured source information with its provenance. Document the
   tables/views, field meanings, units, relationships, time semantics, and unknown
   states needed by a consuming agent. The path must work from a clean checkout
   with documented setup. Keep the batch result associated with
   its input even when outputs arrive out of order. Preserve explicit failures
   and usable partial information. Evidence with an unknown original fetch time
   must remain retrievable without inventing a dated price or stock observation.

   Done when ingestion runs against the scratch database and actual agent query
   results match the inspected source meaning, including the excluded matches.

5. **Verify consumption through agent queries.** Use the documented database
   structure to retrieve and relate the stored information, including quantities,
   price coverage, source evidence, and unknowns. Trace the retrieved information
   back to the captures and interpretation. Fix discrepancies across parsing,
   storage, or querying before calling the slice working. Use focused checks to
   preserve that behavior. Keep inputs and model results inspectable; credentials
   never belong in artifacts.

   Done when the bounded information is usable by a consuming agent from the
   database and its documented meanings, without relying on the ingestion
   agent's chat explanations. Passing checks alone does not complete this step
   or establish wider coverage.

## Expand the same working path

After the first working delivery, extend the implementation in this order. Keep
each extension small enough to demonstrate through the same agent query path.

1. **Continuity and real changes.** Reprocess the same capture, reorder model
   output, interrupt/retry a batch, and use another real capture of the same
   source offer. Preserve past interpretations and prices without duplicating
   purchases or attaching a price to a different component. Distinguish an
   interpretation change from a real price/availability change. Include known
   unavailable and unknown-availability cases in the returned answers.
2. **Broader information and categories.** Add genuinely different source and
   hardware shapes, configuration choices, units, currencies, and price kinds.
   Show that a new meaningful category can be represented and queried without
   reproducing a fixed legacy taxonomy. Verify comparable configurations and
   deliberate near matches with source evidence.
3. **Useful cost apportionment.** Keep the seller's purchase amount intact.
   Demonstrate a separately identified allocation of acquisition cost across
   contents using an explicit method and supporting evidence. Show quantities,
   missing support, and uncertainty. An allocated cost must not appear as a
   seller's component ask or a separately purchasable offer. Decide the method
   from a real buying question; no arbitrary division or component valuation
   machinery is assumed.
4. **Routine intake.** Connect an existing scraper's retained capture output to
   the demonstrated path. Preserve its source identity and time semantics.
   Address actual acquisition gaps only after confirming what the existing
   collectors provide. Legacy corpus migration comes after the new path works.

History, varied categories, and cost apportionment remain required work even
though the first delivery is deliberately smaller.

## Incorporation into the main project

Use the demonstrated behavior to choose an initial improvement for
MooseGooseConsulting/TechdealsHandoff. Inspect the actual writer and consumer
contracts at that point; production's current schema does not dictate this
spike's representation. Compare both paths on the same real captures and show
what the affected consumer receives, what history changes, and how to roll back.

Bring that bounded improvement through a reviewed PR. Code incorporation,
deployment, data repair, and retiring an old writer are separate actions. This
plan does not authorize production changes. A new schema need not be imported
wholesale, and finishing the spike does not require polishing every prototype.

## Keep the work bounded

Keep the problem statement and implementation plan aligned as decisions develop.
Add source, examples, dependencies, and focused checks as the working slice needs
them. Scoring systems, confidence numbers, rankings, generic evaluation tooling,
bulk legacy imports, new collectors, and production deployment are outside the
initial slice. Task progress and review replies belong in issues and PR threads.
