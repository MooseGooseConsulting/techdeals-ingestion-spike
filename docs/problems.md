# Problems this project must solve

Status: draft, 2026-10-16. This records the confirmed project purpose and
candidate behaviors for design review. Detailed modeling choices below are
proposals where noted, not approved implementation requirements.

## Purpose

We have heterogeneous hardware information that needs to become useful for
sorting, querying, tallying, and buying analysis by other agents.

The basic job is to retain the scraped item, prices, currency, location, seller,
condition, availability, descriptions, specifications, dates, and other source
information in a database. Parsing makes messy fields usable; a complete
component interpretation is not a prerequisite for storing the scraped record.

The difficult part is preserving its meaning from the original source through
to the data retrieved by a consuming agent. The intended result is a functioning
ingestion pipeline and database whose demonstrated improvements can enhance
the main TechDeals project.

Other agents are the database consumers. They need queryable scraped facts,
their source and time context, and a clear account of unknown or conflicting
information. Preserve associations and quantities when a source states them;
whether they need a shared relationship model is an open design question. The
owner is not expected to query the database personally. A fixed menu of
human-facing buying questions does not define the scope. The data must be
understandable from its documented structure and meanings without depending on
the ingestion agent's conversation history.

The [data contract](data-contract.md) describes the proposed handling of scraped
records, dates, parsing, identity, and deduplication. The [implementation plan](plan.md)
describes how we propose to address these problems. Design and sequence can
change as we learn; only source-faithful retention, useful retrieval, and
traceable dates and rescrapes are established outcomes. The detailed structures
and analysis ideas below need evidence and design review.

## P1. Heterogeneous information

A standalone part, a configured server, a lot, and a multi-item sale post
describe hardware and purchases differently. Flattening them into the same
record can erase the differences needed to understand or compare them.

The information also comes through many different scrapers, APIs, feeds,
HTML/browser collectors, and exports. Bright Data is one possible collection
method. Intake must preserve and organize information across these routes
without assuming a single provider's fields or response format.

The number and arrangement of records vary independently of the collection
method. One payload may contain hundreds of products and variants; one post may
contain several separately priced items; one auction may have its description
and bid information in separate responses. Supporting several sites with the
same response shape does not establish that these differences are handled.

**Required result:** other agents can retrieve, sort, query, and tally useful
information from these different structures together while retaining the context
that determines what each item and purchase means. This must work across varied
real source and hardware shapes from the first working delivery. Establish the
initial representation against several different shapes together, including
one input producing many records and several inputs describing the same listing.
One server family or one collector's layout cannot establish that result.
The proposed solution is a shared LLM-backed parser, supported by general format
readers and reliable storage behavior. It must be tried against unfamiliar
sources. Requiring a handwritten parser or field mapping for every new source
would leave the central problem unsolved.

## P2. Multiple items and source-stated associations

One payload or URL may contain several items, a system, a lot, or selectable
configurations. These shapes can affect which source-stated price belongs to
which item. The available captures must show which distinctions are actually
present and useful before they become common database fields.

**Confirmed behavior:** retain each scraped item and price with its source
evidence. Preserve an association, quantity, or unit when the source states it;
keep it unknown when the source does not. Do not infer that a component is
included, installed, separately purchasable, or available in a quantity. Whether
these facts need a general relationship model remains open until real captures
and downstream needs are reviewed.

## P3. Source-stated price information

An amount may be attached to an item, group, listing, or selectable
configuration. Asks, bids, sold prices, and acquisition costs are different
source facts when the source identifies them. Currency, original price text, and
stated conditions affect their meaning.

Do not turn a listing price into an inferred component price. Cost apportionment
or other derived allocations are separate analysis ideas, not an ingestion
requirement; consider them only if a concrete downstream need establishes them.

**Confirmed behavior:** retain the stated amount, currency when present,
original price text, source reference, and source-stated price context. Preserve
ambiguity when the source does not establish which item or configuration an
amount covers.

## P4. Meaningful matching and comparison

Similar names, a shared chassis, or a shared CPU do not establish equivalent
configurations. Missing discriminating facts can make a plausible match
misleading. The attributes that matter depend on the hardware and the consuming
agent's task.

**Required result:** consuming agents can query purchases against their own
requirements and exclude misleading near matches. The data preserves differences
in installed configuration, condition, purchase quantity, and other relevant constraints.
Incomplete identity remains explicit rather than becoming a guessed match.

## P5. Source evidence, dates, and history

Every selected fact or price needs a traceable source. Fetch time, publication
time, import time, and the time a price was observed describe different events.
Reinterpreting a capture is also different from observing a real market change.

Rescraping a listing must not create another item or repeat the same information
in ordinary query results. An unchanged scrape confirms that the listing was
seen again. A changed price, location, specification, availability, or other
source value needs history. Reimporting the same scrape is a retry, not another
sighting. Different sellers offering the same model are distinct listings.

An item priced at $1,640, then $1,590, then $1,640 has three chronological price
states. The return to $1,640 must survive deduplication even though that value
appeared before. Repeated sightings must not become independent items or price
samples in aggregations.

**Required result:** agent queries expose the supporting source and known
time context. Missing dates stay unknown. Repeated captures and revised
interpretations preserve price and availability history without duplicating
purchases or attaching old prices to different components. Consuming agents can
distinguish historical evidence, current observations, and unavailable or unknown inventory.

Ordinary listing queries expose each source listing/variant once with its latest
known state and observation date. Historical queries expose actual changes and
their dates. Unchanged sightings retain freshness without duplicating the full
listing information. Reprocessing an old scrape changes its interpretation, not
its observation date. Source timestamp meanings must be established per scraper;
import time never substitutes for scrape time.

## P6. Useful incomplete information and visible failures

Missing identity, an unknown fetch date, an ambiguous price, conflicting claims,
or a failed item interpretation must not make the remaining useful information
disappear. A successful model request does not establish a usable interpretation.

**Required result:** original evidence and useful partial interpretations remain
retrievable. Unknowns, conflicts, failures, and exclusions have explicit
dispositions. Consuming agents can retrieve known information and the limits on
using it; no missing fact is silently invented to make a record fit.

## P7. Categories that develop with the information

Hardware types and useful groupings develop as new information arrives. A fixed
taxonomy or one source's field layout can prevent the next useful category
from being represented or queried.

**Required result:** new hardware and meaningful categories can be organized and
queried while preserving their relevant attributes and relationships. The
database supports category-specific requirements without assuming a legacy
schema or forcing unlike configurations into the same comparison.

## Working boundaries

Use batched free-model parsing. Existing captured evidence from the different
collectors provides the starting material. Their outputs can be improved where
needed. A custom Bright Data scraper is one available option; each source can
use the collection method appropriate to it.
Broad legacy migration waits until the new path works. Development
must protect the existing cluster and production data. Production incorporation
follows demonstration of useful behavior and review of the affected consumers
and history.

## Directions already rejected

Earlier TechDeals work proposed these. They are recorded here so they are not
reopened by a later agent reading the reference repositories. Where a rejection
supersedes an owner's earlier verbatim request, the later, narrower instruction
governs. See [idea-triage.md](idea-triage.md) for the full disposition list.

- **Cost apportionment / dividing a lot price across components.** An earlier
  brief asked that a multi-item posting's total be apportioned. Later design work
  prohibits inventing unit prices: retain the stated amount and its scope, and
  leave a component's price explicitly ambiguous (`bound`) when the source does
  not state it (P3). Do not build a division feature.
- **A fixed product taxonomy as the schema.** The twelve-category projection set
  and legacy identity graph are production reference, not this project's DDL.
  Categories develop with the information (P7); adding one is inserting data.
- **Scraper-first or collector-per-source implementation.** Collection is not the
  bottleneck. Prove the shared parser and storage from existing captures first;
  add a source-specific route only for a demonstrated gap (P1).
- **Model-generated identity or an arbitrary confidence score.** Listing identity,
  deduplication, dates, and merges use ordinary code and source/native keys; a
  model-produced identifier or confidence number does not establish either
  (P5, P6).

## Acceptance probes

The project exists to answer useful buying and analysis questions, not to pass a
schema review. These probes (from the v2 goal statement and brief; reference
material, not a schema mandate) exercise the path end to end. They demonstrate
access; they do not limit the database to a fixed list.

| Probe | Exercises | Covered by |
|---|---|---|
| Cheapest N units of spec S available now | decomposition, per-unit or bound prices, identity, live availability | P1, P2, P3, P4 |
| What did this cost over time | append-only observations, price-kind separation | P3, P5 |
| What is this lot actually worth | line hierarchy, honest price basis (bounds, never allocated) | P2, P3 |
| What did we miss | provenance, quarantine and failures as first-class states | P5, P6 |

Production consumer constraints (`TechdealsHandoff/docs/NORTH_STAR.md` G1–G5,
AG1–AG9) are checks for the later incorporation decision. They are not
requirements this spike implements.
