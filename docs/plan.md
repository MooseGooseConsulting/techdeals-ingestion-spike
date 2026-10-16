# Bounded parser comparison and queryable evidence

Status: implementation in progress, revised 2026-10-16 after the owner's instruction
to start substantially smaller and retain reasoning. The first review gate is the
two-record extraction below. The larger candidate comparison remains subsequent
experimental work, not an established working parser.

Read [the required outcomes](problems.md) and [the data contract](data-contract.md)
first. Other agents consume documented SQL facts and relationships. The parser
agents described here interpret captured evidence; they are distinct from those
consumer agents. This exercise implements a bounded comparison of two parser
candidates and actual PostgreSQL storage. It does not solve every broader
TechDeals problem or establish production readiness.

## First gate: two small records, one extraction

Start with package `small-a`: one complete native ServerPartDeals product object
(2,494 bytes) and one complete DiscTech product card plus its corresponding native
JSON-LD Product (9,090 bytes). They retain their parent source hashes, explicit
selection rules, and unknown acquisition times. Their independent expectations
are evaluator-only.

Send both complete records together in one typed extraction request. Bypass
layout discovery and inspection-tool loops. Persist raw captures first; validate
the result, write valid units to Neon, and inspect every output field, original
price, source-date meaning, and SQL round trip against these two inputs. Keep
unknown seller/location/date facts unknown rather than filling them for appearance.

Use `run --package small-a --arm staged --direct`. Inspect the transcript before
expanding. A schema correction is visible and counted; failed output stays failed.
Only after this small path produces useful, inspected records should the larger
A/B development packages or held-back comparison run. The existing experimental
candidate implementations do not constitute proof of that broader behavior.

The earlier development attempts exposed two concrete failures: full-body layout
discovery exhausted 600 seconds before extraction, and serial root paging exhausted
twelve tool requests before final output. Their retained transcripts are evidence
for revising the execution design, not successful ingestion examples.

## Shared implementation boundary

Other agents query the database and use the information in their own tasks.
The first delivery is an ingestion pipeline and queryable database with
documented data meanings. Its confirmed consumer requirements are retrieval of
scraped facts with source and time context, visible unknowns, and deduplicated
rescrapes with history. A general relationship model is an open design question.
Start with the actual scraped information: item, price, currency, location,
seller, condition, availability, description, specifications, dates, and the
remaining source fields. Retain the original record. Structured values can be
mapped directly; batched LLM interpretation supplies usable information from
fields that need it. Incomplete interpretation does not block basic intake.
The owner's personal use of a query command or buying interface is not a
requirement.

Commit model `nvidia/nemotron-3.5-lightning:free` and the OpenRouter endpoint
`https://openrouter.ai/api/v1` in configuration. There is no alternative model or
endpoint fallback. Reasoning is explicitly enabled; do not disable it as a latency
shortcut. Lightning's free route supports tools but does not advertise native
`response_format`/`structured_outputs`. The harness uses PydanticAI `ToolOutput`
and validates returned arguments locally, rather than claiming provider-enforced
JSON Schema. The actual small call must establish availability and suitability.
Credentials and connection details come from Doppler-injected
process environment; the application must not load `.env` files. Secrets never
belong in committed captures, prompts, logs, or reports.

First probe this exact model through the intended harness for structured output
on multiple inputs and for tool calls followed by the typed final result. Save
request configuration, observed behavior, and failure details without secrets.
A catalog entry, successful text response, or another model's capability does
not establish compatibility. A failed probe is a reported limitation requiring
an explicit revised decision, not silent substitution.

## Frozen evidence packages

Commit raw bytes before parsing. Freeze hashes, acquisition provenance, selection
rules, and native fragment references separately from evaluator expectations.
Keep the independently inspected oracle inaccessible to both parser candidates.
Preserve complete selected native units and their context; do not derive inputs
from old normalized outputs. A selected fragment must identify its parent and
selection, rather than presenting itself as an untouched whole capture.

| Package | Purpose | Selected material |
|---|---|---|
| [P1: heterogeneous information](problems.md#p1-heterogeneous-information) | First deliverable, steps 1–6; broader information extension | Several structurally different inputs, including one-to-many and many-to-one cases, become useful query results through the same path |
| [P2: multiple items and source-stated associations](problems.md#p2-multiple-items-and-source-stated-associations) | Inspect across the first delivery; settle shared fields after source review | Preserve each scraped item and price, plus source-stated associations and quantities; do not infer missing purchase facts |
| [P3: source-stated price information](problems.md#p3-source-stated-price-information) | First deliverable | Retain amounts, currencies when present, original text, source references, and any source-stated price context |
| [P4: meaningful matching and comparison](problems.md#p4-meaningful-matching-and-comparison) | First deliverable, steps 2–6; broader information extension | Queries return suitable purchases and exclude misleading near matches using the relevant configuration facts |
| [P5: source evidence, dates, and history](problems.md#p5-source-evidence-dates-and-history) | First deliverable, including the [data contract](data-contract.md#listing-identity-and-repeated-scrapes); broader continuity cases afterward | Ordinary queries deduplicate source listings; dated sightings, actual changes, and revised interpretations remain distinguishable |
| [P6: useful incomplete information and visible failures](problems.md#p6-useful-incomplete-information-and-visible-failures) | First deliverable, steps 1–6; retry/interruption work in the continuity extension | Partial evidence remains retrievable, with explicit unknowns, conflicts, failures, and exclusions |
| [P7: developing categories](problems.md#p7-categories-that-develop-with-the-information) | Broader information and categories extension | New meaningful categories can be represented and queried with their relevant attributes and relationships |

Exact paths and hashes belong in the committed corpus manifests. Freeze C before
prompt/preparation development and run both arms against the same frozen bytes.
An authored feed, enriched/authored auction fixture, or derived CSV can test a
behavior but does not establish real-source coverage. Real multi-item sale-post
and split-auction evidence, verified observation-date semantics, and a confirmed
non-Bright-Data acquisition route remain gaps before claiming the broader first
delivery works. Different retailer names or file formats do not prove collection
route or the missing structural cases.

## The two callable candidates

The staged candidate conditionally discovers layout when general preparation
cannot already identify usable regions. It then extracts batches, validates the
typed result and evidence references, and permits one correction per stage within
the shared budget. Discovery identifies evidence regions and grouping; it does
not eliminate interpretation of each new or changed entry. Target eight units
per extraction batch, retaining enclosing context and explicit dispositions for
unattempted units. A package may require several batches.

The tool-reading candidate runs a scoped loop exposing only `list_children`,
`read_region`, batched `read_regions`, and `search_capture` over the same package's capture allowlist. It chooses
what to inspect and returns the same typed final output. It cannot browse, read
other packages, see evaluator files, or fetch arbitrary paths. Validate tool
arguments and charge failed calls to the tool-attempt budget. Tool reading and
staged preparation are compared with their actual exposure recorded; equal
source access does not imply they saw identical bytes.

| Limit | Per-package requirement for each candidate |
|---|---|
| Model requests | At most 12, including discovery, extraction, tool-loop requests, validation corrections, and final generation |
| Tool attempts | At most 64, including invalid or failed attempts |
| Tool result page | At most 16 KiB; explicit pagination, no concealed truncation |
| Prepared request | At most 96 KiB and must fit the actual model context with prompts, tools, and output allowance |
| Unique source exposure | At most 512 KiB, with repeated exposure separately accounted for |
| HTTP timeout / package wall time | 90 seconds / 600 seconds |
| Corrections | At most one per stage, still inside all package caps |
| Automatic retries | Zero HTTP or job retries |

Stop with explicit budget/context/failure dispositions when a limit prevents
completion. Do not silently truncate source material, omit failures, replace a
batch with unreported per-entry requests, or continue a correction outside the
budget. A deliberate later rerun is a new experiment, not an automatic retry.

## Implementation sequence and acceptance

1. **Freeze evidence and expectations.** Inspect development and holdout material,
   record native references, expected facts, purchase scope, source dates and
   unknowns. Preserve authored/derived labels and the real-evidence gaps above.
   Done when both arms can receive the same parser-safe packages while the
   independent expectations remain evaluator-only.
2. **Define contracts and generic access.** Establish typed inputs/results,
   dependency validation, generic format readers, bounded region tools, and
   shared accounting. Capture raw bytes durably before any interpretation.
   Done when unknown sources enter through these interfaces without semantic
   source adapters and invalid references or budget overruns have visible outcomes.
3. **Probe and implement both candidates.** Probe the committed model, then run
   the staged and tool-reading callables on A and B. Preserve prompts, versions,
   requests, typed outputs, errors, and exposure accounting. Inspect completeness
   and price association against evidence; schema validity alone is insufficient.
4. **Persist and demonstrate SQL.** Use existing Neon project
   `late-wave-13685431`, parent main branch `br-restless-smoke-b4qofs8r`, a new
   child branch `dev/ingestion-exercise`, and a fresh `ingestion_spike` database.
   Do not create another Neon project or local PostgreSQL substitute, or adopt
   existing production schema as authority. Apply the smallest representation
   preserving immutable evidence, observations, interpretations, and scoped
   exact numeric prices. Demonstrate real SQL reads and the continuity checks
   below; local validation is not proof of remote persistence.
5. **Run the frozen paired comparison.** Run C once per arm without tuning on
   it. Compare actual results and SQL reads with the independent oracle. Report
   expected facts, omissions, wrong associations, unsupported values, requests,
   tool attempts, tokens, elapsed time, source exposure, errors, and every
   unattempted case. Keep differences inspectable, with no aggregate score,
   confidence number, or general evaluation framework. If C informs changes,
   it becomes development evidence; a later unfamiliar-source claim needs a
   newly frozen holdout rather than replacement of inconvenient failures.
6. **Review and report the bounded result.** Independently review the contracts,
   evidence separation, parser comparison, storage behavior, and observed results.
   Fix valid findings and state exactly which behaviors were demonstrated and
   which remain unestablished. A green check or successful model response alone
   does not complete the exercise.

## Requirements to checks

Checks refer to required evidence, not claims that these checks have passed.

| Requirement | Demonstration required |
|---|---|
| P1 heterogeneous input / P7 extensibility | Both candidates ingest frozen varied shapes through generic readers; additional facts remain queryable without a fixed hardware taxonomy |
| P2 contents and purchase structure | Oracle-to-output-to-SQL traces for quantities, included/optional/absent components, variants, and separate offers |
| P3 scoped prices | Exact numeric amounts, currency, kind, quantity, and scope retain their evidence and correct item/configuration association |
| P4 useful matching | SQL retrieves supported matches and excludes near matches using stored attributes and explicit unknowns |
| P5 history and identity | Retry idempotence; unchanged new scrape freshness and result reuse; A–B–A price history; late old import; parser rerun without new observation; separate sellers |
| P5 uncertain time | Unknown-date evidence is queryable without becoming current; equal-date incompatible observations remain explicit conflicts |
| P6 partial failure | Raw bytes survive failure; dependency-closed results publish atomically; independently valid results survive another unit's failure |
| Source traceability | Every accepted price/fact/relation resolves to allowed capture evidence; parser cannot access evaluator oracle |
| Practical bounds | All stage requests, tool attempts, token/time/exposure accounting and stopped/unattempted cases appear in the paired report |

1. **Assemble and inspect the initial inputs before designing the parser or
   schema.** Cover each [initial structural case](data-contract.md#different-input-shapes-through-the-same-path):
   a structured listing, products with multiple variants, repeated HTML entries,
   a feed or post with multiple items, delimited or historical rows, and linked
   responses that jointly describe one listing. Select a bounded set of captures
   for those cases; there is no arbitrary fixed capture count and no requirement
   to support every source/format combination. Include different hardware and
   listing structures within that set, such as a configured system, standalone
   part or lot, and a multi-item post.

## Delivery and later work

   Include a confirmed collection route independent of Bright Data. Identify
   native fields and metadata rather than inferring acquisition method from
   source name or file format. Inspect items, prices and their source-stated
   associations, currency, location, seller, condition, availability,
   specifications, and dates. Preserve missing values and contradictions
   explicitly. Do not use a previous parser's
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
   Preserve distinctions and quantities when the source states them. Do not
   infer whether a component is installed, included, or separately purchasable.
   Identify which source-stated facts agents need to retrieve together, and
   leave the need for a general relationship model open until source evidence
   and downstream use have been reviewed.

   Done when each structural case has explicit expected records, including
   unknowns and conflicts, and representative SQL reads can establish whether
   those meanings survive. Example queries demonstrate access; they do not limit
   the database to a fixed list of questions.

3. **Define the provisional parser output schema.** Use the
   [initial output target](data-contract.md#initial-parser-output-target) and the
   inspected expected records to define the structured result accepted from
   the model. Cover common item fields, multiple prices and their source-stated
   context, extensible attributes, source dates, evidence references, and
   unresolved information. Preserve source-supported associations.
   Keep trusted collection metadata supplied by the program alongside the
   result. Define the output from all initial shapes together; the database
   schema remains provisional until the parser results have been inspected.

   Done when the expected records fit the output structure without invented
   facts, lost information, or a fixed hardware taxonomy.

4. **Build the shared batch parser first.** Python and Instructor are proposed
   starting tools for batched extraction; first
   check compatibility with the selected free endpoint and its output mode.
   Keep this choice explicit in the implementation PR. Add dependencies only
   for work this experiment actually needs. A documented executable reads the
   selected saved captures, prepares batches, and writes structured results with
   their input context and evidence references to inspectable files. Run varied
   development inputs together, including partial or failed interpretations.
   Reuse a compatible validated interpretation before making a model call when
   its input content and context are unchanged. Demonstrate this with a repeated
   run, and demonstrate that changed content is interpreted as needed. Keep
   this initial reuse behavior inspectable in the file-based experiment;
   database identity and history are added in the next step.
   Check that every expected item and price survives and that source-stated
   associations are retained. Preserve dates and unknowns; validate input
   references and established structured values. Refine the output schema
   against actual failures before settling database tables.

   Establish the expected ingestion volume from the existing collectors. Record
   the inspected run's input size, model request count, elapsed time, and any
   failures or retries so the per-entry approach can be assessed against that
   workload. Multiple entries in a request must not conceal omitted items.

   Done when the executable runs with the selected free endpoint in batches,
   and inspected development results establish the intended extraction behavior
   across the initial shapes. A successful request or valid JSON alone is not
   completion. Unfamiliar-source behavior is checked with the frozen set below.

5. **Add persistence and run the complete path.** Choose the smallest database
   representation that preserves the inspected parser results, original
   evidence, and required date/identity/history behavior. Do not begin with the
   previous tables or recreate its module tree. Use PostgreSQL on an isolated
   branch of an existing Neon project. A TimescaleDB extension remains a
   separate workload decision; time-series requirements do not depend on it.
   Every stored field needs a concrete purpose in agent consumption or retaining
   source evidence. Extend the same batch entry point to store the validated
   information with its evidence. Preserve usable structured source
   information with its provenance. Document the tables/views, field meanings,
   units, relationships, time semantics, and unknown
   states needed by a consuming agent. The path must work from a clean checkout
   with documented setup. Exercise all initial shapes in the same run, including
   multiple records from one payload and one listing supported by multiple
   payloads. Keep the batch result associated with its input even when outputs
   arrive out of order. Preserve explicit failures and usable partial
   information. Evidence with an unknown original fetch time
   must remain retrievable without inventing a dated price or stock observation.
   Retain unmapped or failed entries with an explicit disposition. A new shape
   must not silently lose records or require guesses to fit the representation.
   Use ordinary code for database identity, deduplication, time handling, and
   deterministic calculations. Investigate source-specific code only when the
   shared approach has a demonstrated limitation.

   Done when ingestion runs against the scratch database and actual agent query
   results match the inspected source meaning, including the excluded matches,
   and the [data contract's rescrape cases](data-contract.md#listing-identity-and-repeated-scrapes) behave as specified.

6. **Verify consumption through agent queries.** Use the documented database
   structure to retrieve the stored information, including prices, source
   evidence, dates, and unknowns. Trace the retrieved information
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
3. **Consider cost apportionment only if a concrete need emerges.** This is not
   part of the ingestion requirement. If a downstream task later needs an
   allocation, define its method and evidence separately from the seller's
   stated price, and review that design before implementation.
4. **Routine intake.** Connect existing scrapers' retained capture outputs to
   the demonstrated shared path. Preserve source identity and time semantics
   across collection methods.
   Address actual acquisition gaps only after confirming what the existing
   collectors provide. Legacy corpus migration comes after the new path works.

History and varied categories remain work beyond the first delivery. Any
cost-apportionment feature requires a separate demonstrated need and design.

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
