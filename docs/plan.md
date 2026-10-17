# Build the ingestion path

Revised 2026-10-16 to address the design before further PR-review work.
[The design correction](design.md) defines the implementation target.
The direct runtime replaces the graph prototype; passing its local checks does
not complete this plan. README.md records the current demonstration status.

## Goal

Store scraped items, all stated prices, location, seller, condition,
availability, specifications and dates as useful database records for other
agents. Preserve native inputs, multiple entries in one source, date meanings,
and repeat-scrape history. Use one shared LLM-backed extraction path rather than
a handwritten semantic parser for each source.

## Implementation sequence

1. **Replace the model-facing graph contract with entries and named fields.**
   Define the actual extraction schema from the field table in design.md.
   Keep prices under the entry they describe and native source dates separately
   named. Move database IDs, capture metadata and processing dispositions out of
   model output. Keep nullable values and flexible source attributes.
2. **Build one direct PydanticAI extraction path.**
   Supply complete native inputs and collection context in a small varied batch.
   Use typed output, a bounded framework-managed correction, and inspectable
   messages. Keep the configured model free and reasoning enabled; supply secrets
   through Doppler. Discovery and inspection tools are not part of this first path.
3. **Persist and inspect the result.**
   Retain raw input first. Store entry fields, exact numeric prices, native
   attributes and source dates in the existing isolated Neon development target.
   Assign application IDs and attach trusted collection timestamps in code.
   Keep unknown retrieval times unknown. Verify actual SELECT results against
   each source field, including missing information and qualifications.
4. **Prove the structural cases.**
   First use the two native inputs already selected as small-a; then a real
   multi-entry source. Verify that one input can yield several correctly priced
   entries and that a bundle price stays with the bundle. Use known linked
   responses together when available; retain the existing real-source gaps.
5. **Prove repeat ingestion.**
   Reimport the same capture, ingest an unchanged later scrape, then a changed
   price. Verify idempotence, freshness, preserved observation dates and history,
   with no duplicate entries in ordinary queries. Match through established
   native identity; leave unsupported matches unresolved.
6. **Review the delivered behavior, then consider incorporation.**
   Show native input, extracted entries and stored rows together. State every
   remaining failure. Only demonstrated behavior should inform a reviewed change
   to the main TechDeals project; production incorporation remains separate.

## Completion of the next slice

The initial extraction checkpoint is useful, inspected entries and prices in
Neon with correct source/date context. Completion of the ingestion slice also
requires the multi-entry and repeat-ingestion demonstrations in steps 4 and 5:
same-capture idempotence, unchanged-scrape freshness, and dated price changes.
Source-to-output-to-SQL inspection is the acceptance evidence. A provider response,
schema pass, green CI result, or completed PR-review queue is insufficient on its own.

Preserve unknown currency and seller where the source does not establish them.
Retain stock qualifications and distinguish vendor from seller, publication from
retrieval, supported hardware from included hardware, and purchase controls from
available stock. Report source truncation without completing its text.

## Existing evidence and implementation

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

The former two-arm comparison plan is retained in
[the previous committed plan](https://github.com/MooseGooseConsulting/techdeals-ingestion-spike/blob/99a41ea18b9f3837cf09d8d136d3de5223f48cec/docs/plan.md).
Its discovery/tool protocols and paired benchmark are no longer the immediate
implementation goal. Frozen corpus files and their provenance are unchanged.

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
| Retained native package | At most 1 MiB; this admits every frozen package and is separate from material presented to the model |
| Prepared view exposure | At most 512 KiB of distinct views, with repeated exposure separately accounted for |
| HTTP timeout / package wall time | 90 seconds / 600 seconds |
| Corrections | At most one per stage, still inside all package caps |
| Automatic retries | Zero HTTP or job retries |

Stop with explicit budget/context/failure dispositions when a limit prevents
completion. Do not silently truncate source material, omit failures, replace a
batch with unreported per-entry requests, or continue a correction outside the
budget. A deliberate later rerun is a new experiment, not an automatic retry.

The exposure cap conservatively counts locator-view UTF-8 offsets for tool reads,
and serialized native records/discovery previews for staged preparation. Different
overlapping views can count the same source bytes more than once; these counters
are not an exact measure of unique original bytes. Total and repeated submitted
view bytes are reported separately. SDK retransmission of conversation history is
outside these view counters and remains subject to request/context limits and
reported token usage. The direct path supplies its whole native package once and
must fit the smaller prepared-request allowance.

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

## Delivery and later work

The numbered delivery steps that used to continue here described a retired
Instructor and two-candidate sequence. They duplicated the current
implementation sequence and still proposed a different parser stack, so that
list is removed. Later extensions are the sections below.

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
