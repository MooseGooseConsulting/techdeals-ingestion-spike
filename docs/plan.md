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
Start with the actual scraped information: item, price, currency, location,
seller, condition, availability, description, specifications, dates, and the
remaining source fields. Retain the original record. Structured values can be
mapped directly; batched LLM interpretation supplies usable information from
fields that need it. Incomplete interpretation does not block basic intake.
The owner's personal use of a query command or buying interface is not a
requirement.

| Problem | Where this plan addresses it | What must be demonstrated |
|---|---|---|
| [P1: heterogeneous information](problems.md#p1-heterogeneous-information) | First deliverable, steps 1–4; broader information extension | Several structurally different inputs, including one-to-many and many-to-one cases, become useful query results through the same path |
| [P2: contents, quantities, and purchase structure](problems.md#p2-contents-quantities-and-purchase-structure) | First deliverable, steps 1–5 | Data retrieved by agents preserves contents, quantities, installed/optional/absent facts, and what can be bought separately |
| [P3: price scope and cost apportionment](problems.md#p3-what-prices-cover-and-how-costs-are-apportioned) | First deliverable for stated purchase prices; cost apportionment extension for attributed costs | Amounts cover the correct purchases; justified allocations remain separately identified and traceable |
| [P4: meaningful matching and comparison](problems.md#p4-meaningful-matching-and-comparison) | First deliverable, steps 2–5; broader information extension | Queries return suitable purchases and exclude misleading near matches using the relevant configuration facts |
| [P5: source evidence, dates, and history](problems.md#p5-source-evidence-dates-and-history) | First deliverable, including the [data contract](data-contract.md#listing-identity-and-repeated-scrapes); broader continuity cases afterward | Ordinary queries deduplicate source listings; dated sightings, actual changes, and revised interpretations remain distinguishable |
| [P6: useful incomplete information and visible failures](problems.md#p6-useful-incomplete-information-and-visible-failures) | First deliverable, steps 1–5; retry/interruption work in the continuity extension | Partial evidence remains retrievable, with explicit unknowns, conflicts, failures, and exclusions |
| [P7: developing categories](problems.md#p7-categories-that-develop-with-the-information) | Broader information and categories extension | New meaningful categories can be represented and queried with their relevant attributes and relationships |

All seven problems remain open. The sequence below is proposed. The initial
structural cases are explicit in the data contract; the exact verified captures
and stored record examples still need to be selected and inspected.

## Data behavior required in the first delivery

Use the [data contract](data-contract.md) for scraped fields, record transformation,
date meanings, parsing, identity, deduplication, and possible scraper changes.
Use one shared LLM-backed parser across collector outputs, with general format
preparation and supplied source metadata. New sources should be usable without
writing a parser or field mapping for each one. Bright Data-specific metadata
is not required of other scrapers. Keep original source identity separate from
collection method, including for deduplication.
Its [rescrape cases](data-contract.md#listing-identity-and-repeated-scrapes) are
part of the first working delivery. Select the open design choices there before
finalizing the schema or collector output.

## Runtime configuration

Use free models only, with the chosen model and endpoint in committed parser
configuration. Supply credentials and connection details through
Doppler-injected process environment variables. The application must not
automatically load a local `.env` file. Keep credentials out of source files,
captured evidence, model request logs, and generated artifacts.

Database development uses an isolated branch of an existing Neon project, as
specified in the implementation steps below.

## First deliverable: a shared path across varied inputs

The proposed executable boundary is batch ingestion of saved collector outputs
into PostgreSQL, consumed through documented SQL tables/views. The initial
delivery takes several different input shapes through the complete path
together. Routine connections to running collectors follow the demonstrated
import path.

The central hypothesis is that a good shared LLM parser can turn this varied
content into useful records. The initial shapes below are evidence for testing
that parser, not separate source-parser implementation projects. Generic input
reading, validation, and database bookkeeping support it.

1. **Assemble and inspect the initial inputs before designing the parser or
   schema.** Cover each [initial structural case](data-contract.md#different-input-shapes-through-the-same-path):
   a structured listing, products with multiple variants, repeated HTML entries,
   a feed or post with multiple items, delimited or historical rows, and linked
   responses that jointly describe one listing. Select a bounded set of captures
   for those cases; there is no arbitrary fixed capture count and no requirement
   to support every source/format combination. Include different hardware and
   purchase structures within that set, such as a configured system, standalone
   part or lot, and a multi-item post.

   Use the [located source material](data-contract.md#existing-material-to-inspect-for-these-shapes)
   as inspection leads. Record exact fixture paths, source and collection
   provenance, and source excerpts beside the selected examples so a clean
   checkout can use them. Check which files are original captures, authored
   fixtures, or derived exports. Authored cases can expose a needed behavior;
   they do not establish that a real source works. Find verified real examples
   for the structural cases before claiming the initial coverage.

   Include a confirmed collection route independent of Bright Data. Identify
   native fields and metadata rather than inferring acquisition method from
   source name or file format. Inspect item, prices and their scope, currency,
   location, seller, condition, availability, specifications, and dates. Preserve
   missing values and contradictions explicitly. Do not use a previous parser's
   output or reference spreadsheet as expected truth.

   Choose a finite development set and a separate, frozen set of held-back
   sources before prompt and preparation development. Include structural
   differences in both, so the initial design is not built around one layout.
   Record expected items, prices, associations, dates, and explicit unknowns
   independently of parser output. Label constructed edge cases separately
   from real-source coverage.

   Done when the initial set and its independently inspected meanings are
   recorded, with the matrix covered and any missing real evidence visible.
   An evidence gap remains work to resolve before calling the delivery working.

2. **Describe stored results for all initial shapes together.** Write concrete
   input-to-record examples before committing to a schema: which entries become
   which records, which responses are associated, which price covers which
   item/variant, and which source references and dates survive. Start with the
   scraped facts that other agents need to retrieve. Include additional
   attributes and unknowns rather than designing around one hardware family.
   Preserve distinctions such as installed versus supported hardware,
   per-component versus aggregate capacity, and a bundled component versus a
   separately purchasable item.
   Define their meanings and how agents can retrieve and combine them for their
   own tasks. Link these requirements to the problems above.

   Done when each structural case has explicit expected records, including
   unknowns and conflicts, and representative SQL reads can establish whether
   those meanings survive. Example queries demonstrate access; they do not limit
   the database to a fixed list of questions.

3. **Choose the smallest representation and tools for the varied inputs.** Check
   the required records and relationships against all the examples, then implement
   them. Do not begin with the previous tables or recreate its module tree.
   Use PostgreSQL on an isolated branch of an existing Neon project. Python
   and Instructor are proposed starting tools for batched extraction; first
   check compatibility with the selected free endpoint and its output mode.
   Keep this choice explicit in the implementation PR. Add dependencies only
   for work this slice actually needs. A TimescaleDB extension remains a
   separate workload decision; time-series requirements do not depend on it.

   Done when every stored field has a concrete purpose in agent consumption or in
   retaining their source evidence, and the chosen model request works in a batch.

4. **Build and run the complete path.** A documented batch ingestion entry point
   preserves the selected saved captures and prepares them with general format
   readers. Supply their source references, shared context, and known collection
   metadata to the shared parser. Use batched free-model requests to identify
   items and their information across layouts, retaining explicit structured
   values and returning a common record structure with evidence references.
   Validate the returned information and store it with the source evidence in
   the database queried by other agents. Preserve usable structured source
   information with its provenance. Document the
   tables/views, field meanings, units, relationships, time semantics, and unknown
   states needed by a consuming agent. The path must work from a clean checkout
   with documented setup. Exercise all initial shapes in the same run, including
   multiple records from one payload and one listing supported by multiple
   payloads. Keep the batch result associated with its input even when outputs
   arrive out of order. Preserve explicit failures
   and usable partial information. Evidence with an unknown original fetch time
   must remain retrievable without inventing a dated price or stock observation.
   Retain unmapped or failed entries with an explicit disposition. A new shape
   must not silently lose records or require guesses to fit the representation.
   Use ordinary code for database identity, deduplication, time handling, and
   deterministic calculations. Investigate source-specific code only when the
   shared approach has a demonstrated limitation.

   Done when ingestion runs against the scratch database and actual agent query
   results match the inspected source meaning, including the excluded matches,
   and the [data contract's rescrape cases](data-contract.md#listing-identity-and-repeated-scrapes) behave as specified.

5. **Verify consumption through agent queries.** Use the documented database
   structure to retrieve and relate the stored information, including quantities,
   price coverage, source evidence, and unknowns. Trace the retrieved information
   back to the captures and interpretation. Fix discrepancies across parsing,
   storage, or querying before calling the slice working. Use focused checks to
   preserve that behavior. Keep inputs and model results inspectable; credentials
   never belong in artifacts.

   Run the frozen held-back set through the same path and compare database
   results against its inspected expected records. Count omitted items and
   prices, wrong associations, invented values, and lost dates as failures,
   even when returned records look plausible. If required behavior fails, report
   that limitation and the specific correction or revised experiment needed;
   the shared-parser hypothesis remains undemonstrated for that behavior.
   Tuning against a failed held-back example makes it development material.
   Any later claim about unfamiliar sources requires a new, finite held-back
   set; continually replacing failed examples is not a completion rule.

   Done when the bounded information is usable by a consuming agent from the
   database and its documented meanings across every initial structural case,
   without relying on the ingestion agent's chat explanations, and the frozen
   held-back run demonstrates its required behavior. Passing checks alone does
   not complete this step or establish wider coverage.

## Expand the same working path

After the first working delivery, extend the implementation in this order. Keep
each extension small enough to demonstrate through the same agent query path.

1. **Broader continuity cases.** Extend the first delivery's identity and
   deduplication behavior to more source shapes, reordered model output,
   interrupted batches, and configuration changes. Preserve past interpretations
   and source history without duplicating listings or moving prices to different
   items. Include known unavailable and unknown-availability cases.
2. **Broader information and categories.** Extend coverage beyond the initial
   structural cases with new source and hardware shapes, configuration choices,
   units, currencies, and price kinds. For each new shape, inspect the input,
   describe its expected stored records, and first run it through the same
   parser. Improve general preparation or interpretation where needed; add a
   source-specific rule only for an evidenced limitation. Revisit shared
   semantics when the evidence requires it.
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
4. **Routine intake.** Connect existing scrapers' retained capture outputs to
   the demonstrated shared path. Preserve source identity and time semantics
   across collection methods.
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

Keep the problem statement, data contract, and implementation plan aligned as
decisions develop.
Add source, examples, dependencies, and focused checks as the working slice needs
them. Scoring systems, confidence numbers, rankings, generic evaluation tooling,
bulk legacy imports, and production deployment are outside the
initial slice. Task progress and review replies belong in issues and PR threads.
