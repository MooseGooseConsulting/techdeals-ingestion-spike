# From captured listings to useful answers

Status: initial proposal, 2026-10-16. No implementation or database has been
created for this spike.

## Goal and basis

Make heterogeneous hardware information useful for sorting, querying, and
tallying. A listing may describe a single part, several independent items, a
configured system, a lot, or selectable options. The result must preserve what
is included, what can actually be bought, what each amount covers, where the
information came from, and its history.

The owner requested a new organization repository, a clearly written plan, and
a fresh implementation without mentally inheriting the previous system. They
explicitly rejected carrying scoring and its surrounding machinery into this
spike. This document is an agent-written plan based on those instructions and
the requirements discussed with the owner; it is not an original transcript.

The earlier requirements still matter: batched model calls; varied hardware and
source shapes; component breakdown and justified cost apportionment; dynamic
categories; source and time provenance; preserving price/availability history;
existing scrapers remain in place; legacy migration waits; protect the cluster.
The later model policy is free models only.

## First deliverable: one useful, executable slice

1. **Choose three real captures and inspect them.** Start with a configured
   server, a standalone component or homogeneous lot, and a multi-item sale
   posting. Use the original payloads and source context, not a previous
   parser's output or reference spreadsheet as the expected truth. Check that
   they exercise different purchase structures. Record the exact source
   excerpts needed to explain the expected answers alongside those examples.
   Preserve missing dates and contradictions explicitly.

   Done when each capture has a short, independently inspected description of
   what is offered, its contents/counts, the stated amounts, and unknowns.

2. **Write the questions those captures must answer.** At minimum: what is
   included in this server and what is optional/absent; which items from this
   post can be purchased separately and at what stated prices; and which
   captured purchase satisfies a named component requirement, with its purchase
   quantity and total commitment. A component inside a server may be relevant
   evidence without being a separately buyable offer. Return source evidence
   with the answer. Write down near matches that must be excluded.

   Done when the expected query outputs are concrete enough to compare directly
   with the implementation, including the unknown or unresolved cases.

3. **Choose the smallest representation and tools for that slice.** Sketch the
   required records and relationships against the examples, then implement
   them. Do not begin with the previous tables or recreate its module tree.
   Use PostgreSQL on an isolated branch of an existing Neon project. Python
   and Instructor are proposed starting tools for batched extraction; first
   check compatibility with the selected free endpoint and its output mode.
   Keep this choice explicit in the implementation PR. Add dependencies only
   for work this slice actually needs. A TimescaleDB extension remains a
   separate workload decision; time-series requirements do not depend on it.

   Done when every stored field has a concrete purpose in these answers or in
   retaining their source evidence, and the chosen model request works in a batch.

4. **Build and run the complete path.** One documented command takes the selected
   captures, interprets several listings per free-model request, stores the
   evidence and interpreted information, and exposes the questions through a
   small query command or callable application interface. It must work from a
   clean checkout with documented setup. Keep the batch result associated with
   its input even when outputs arrive out of order. Preserve explicit failures
   and usable partial information. Evidence with an unknown original fetch time
   must remain retrievable without inventing a dated price or stock observation.

   Done when the command runs against the scratch database and the actual query
   results match the inspected source meaning, including the excluded matches.

5. **Walk the result through with the owner.** Show each original listing, the
   interpreted purchase and contents, the stored result, and the query answer.
   Explain every discrepancy and fix the cause before calling the slice working.
   Use focused checks to preserve the demonstrated behavior. Keep the actual
   input and model result inspectable; credentials never belong in artifacts.

   Done when the bounded result is reviewable as a functioning application.
   Passing checks alone does not complete this step or establish wider coverage.

## Expand the same working path

After that walkthrough, extend the implementation in this order. Keep each
extension small enough to demonstrate through the same interface.

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
though the first walkthrough is deliberately smaller.

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

The initial repository contains this plan, a short README, and agent instructions.
Add source, examples, dependencies, and focused checks as the working slice needs
them. Scoring systems, confidence numbers, rankings, generic evaluation tooling,
bulk legacy imports, new collectors, and production deployment are outside the
initial slice. Task progress and review replies belong in issues and PR threads.
