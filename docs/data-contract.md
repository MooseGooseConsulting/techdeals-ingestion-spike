# Scraped records, dates, parsing, and deduplication

Status: draft design, 2026-10-16. These are the data behaviors to establish in
the spike. Database tables and implementation have not been created.

The [problems document](problems.md) explains what needs solving. This document
describes what scraped information means and how ingestion should handle it.
The [plan](plan.md) sets the implementation sequence.

## Sources and collection methods

Information arrives through multiple existing scrapers and collection methods:
direct site/API requests, feeds such as RSS/Atom, HTML or browser collectors,
structured catalog responses, saved exports, and services such as Bright Data.
Bright Data is one acquisition option, including custom scrapers; its response
format and dataset/snapshot identifiers are not a universal intake requirement.

Distinguish the original source from the method used to collect it. The same
eBay listing remains the same source listing whether collected by a local
scraper, a prebuilt service, or a custom collector. Keep the collector and its
run metadata as provenance without making that route the listing's identity.

The design target is one shared LLM-backed parser that can interpret unfamiliar
source content without requiring us to write a parser or field mapping for
every source. Preserve native payloads and their collection context. General
format readers and existing collector metadata prepare the input; the shared
parser produces usable records with source references. A JSON file alone does
not establish how its contents were acquired.

Source identifiers and timestamp meanings still need evidence. Use documented
collector metadata where available and preserve uncertain native fields when
their meaning is unresolved. The LLM must not turn an assumption about a field
into an established identity or observation date. Source-specific rules are
targeted remedies for demonstrated limitations, not prerequisites for intake.

## What comes in

Retain the scraped item, prices, currency, location, seller, condition,
availability, description, specifications, dates, and other source fields.
Preserve the complete original record and its collection context. A complete
component interpretation is not required before this information can be stored.

Different sources may return one listing, a catalog with many products, several
variants, or a post containing multiple items. Preserve the original source
record while exposing the useful individual entries and their associations.

## Different input shapes through the same path

Design the first path against the following shapes together. Collection method,
file format, and information structure are separate dimensions: JSON can hold a
single listing, a whole catalog, or only one fragment of an auction. A CSV row
can still contain ambiguous free text. Different source names alone do not
establish structural variety.

| Shape to include initially | What ingestion must preserve and expose |
|---|---|
| One structured listing | Directly map explicit item, price, currency, location, seller, condition, and date fields; retain unmapped source fields |
| A collection of products with nested variants | Unpack all products and their separately priced variants, retaining shared product context and variant identity; do not treat the response or first product as the only item |
| HTML containing repeated rows or cards | Retain the page and each entry's location within it; distinguish row prices from page-wide text, shipping, and other amounts |
| Feed entries or posts with multiple items in text | Retain entry identity and source dates, separate actual offers from other post types, and associate each item with its stated price or explicit ambiguity |
| Delimited rows or historical exports | Preserve row identity, original columns, date meanings, and price kinds; do not assume an old exported value is a new observation or that derived columns are seller statements |
| Several responses describing one listing | Join description, bid/price, or manifest information only through established source keys and compatible observation context; retain every contributing payload and its own dates |

These are the initial design cases, not an exhaustive list of supported sources.
The first working delivery must exercise them through the same storage and
agent query path. It does not require every combination of collector, format,
hardware type, and price kind. Select source examples that expose several of
those differences at once, and record the remaining coverage explicitly.

The path has three responsibilities:

1. **Preserve and prepare the input.** Keep its native content and collection
   context. General readers handle JSON, HTML, feeds, and delimited files; they
   do not need hardware interpretation rules for each site. Prepare manageable
   batches while retaining entry references, enclosing context, and supplied
   collection metadata. Large inputs must not silently lose entries or context
   through truncation. Related responses can be supplied together when their
   source keys and observation context establish the connection.
2. **Interpret through the shared parser.** Use batched free-model parsing to
   identify items, variants, attributes, and which prices belong to them across
   the different layouts. Copy established structured facts directly when
   available; their presence does not require a new source-specific parser.
   Keep multiple prices with their kinds and scopes instead of selecting one
   arbitrary number. Return source references for the extracted information.
   Preserve partial results and unknowns when an association cannot be
   established. One input may produce many records, and a record may refer to
   several contributing inputs.
3. **Apply shared storage and read behavior.** Listing identity, dates,
   deduplication, history, and source traceability have consistent meanings
   across the inputs. Common facts such as item, prices, location, seller,
   condition, and dates are queryable across inputs. Source-specific attributes
   remain structured and accessible alongside the original payload, with their
   meanings documented as they are mapped.

The shared representation must accommodate repeated prices, variants, and
additional attributes without requiring every hardware type to have the same
columns. Retaining native data also keeps fields available before they have a
shared mapping; that retention alone does not mean those fields are normalized
or usable in cross-source queries.

For a new or changed input shape, retain the capture and explicitly identify
unmapped entries, uncertain associations, and failed interpretations. Do not
silently drop entries or publish guessed fields as established facts. A failure
in one entry must leave independently usable entries retrievable. First try a
new source through the shared parser. Add source-specific code only for a
demonstrated gap that general preparation or parsing cannot handle. Change
shared semantics when inspected information shows a real new requirement.

### Existing material to inspect for these shapes

The eBay capture below is one starting point. Other available material includes
[catalog JSON](https://github.com/MooseGooseConsulting/TechdealsHandoff/blob/1ac8f887db62420a2c2dc2de2ed41597ee7802c6/scripts/ingest/serverpartdeals/fixtures/collections_all_p1.json),
[retailer HTML](https://github.com/MooseGooseConsulting/TechdealsHandoff/blob/1ac8f887db62420a2c2dc2de2ed41597ee7802c6/scripts/ingest/disctech/fixtures/ssd_listing_p1.html),
[an Atom fixture](https://github.com/MooseGooseConsulting/TechdealsHandoff/blob/1ac8f887db62420a2c2dc2de2ed41597ee7802c6/scripts/ingest/reddit_homelabsales/fixtures/sample_atom.xml),
[a CSV export](https://github.com/MooseGooseConsulting/TechdealsHandoff/blob/1ac8f887db62420a2c2dc2de2ed41597ee7802c6/research/ssd-pricing-brightdata/ssd_prices_export_v2.csv),
and separate [auction asset](https://github.com/MooseGooseConsulting/TechdealsHandoff/blob/1ac8f887db62420a2c2dc2de2ed41597ee7802c6/scripts/ingest/govdeals/fixtures/asset_30467_94.json)
and [bid](https://github.com/MooseGooseConsulting/TechdealsHandoff/blob/1ac8f887db62420a2c2dc2de2ed41597ee7802c6/scripts/ingest/govdeals/fixtures/bidbox_30467_94.json) fixtures.

These locate structural examples, not a verified acceptance corpus. The Atom
fixture is authored; the CSV is a derived export; the auction fixtures contain
enriched or authored fields. The inspected catalog has six products but only
one variant per product, so it does not establish support for multiple variants.
Verify acquisition provenance and find missing real examples before claiming
source coverage. Use explicitly labelled constructed cases to check edge
behavior, without presenting them as real captures or treating old normalized
values as expected truth. Existing parsing code is not the starting design.

## From a source record to stored information

The inspected eBay capture for listing `127932828201` illustrates one input
mapping. Its provider-specific fields do not define the contract for other
collectors. These are example meanings, not final database column names:

| Source field | Value in the saved capture | Meaning to retain |
|---|---|---|
| `product_id` | `127932828201` | Source listing identifier |
| `url` | `https://www.ebay.com/itm/127932828201` | Source URL |
| `title` | Supermicro H12SSL-i Motherboard With AMD EPYC 7552 CPU 48Cores 2.2GHz Server CPU | Original item title |
| `price`, `currency` | `$1,640.00`, `USD` | Numeric amount `1640.00`, currency, and original price text |
| `item_location` | `深圳, China` | Location as reported by the source |
| `seller_name` | `motorpartners` | Source seller identity/name |
| `condition` | `Used` | Source condition |
| `availability` | `in_stock` | Source-reported stock state at the observation |
| `available_count` | `10` | Reported available count, distinct from component counts |
| `timestamp` | `2026-10-06T02:22:34.880Z` | Provider-reported timestamp; its precise semantics still need confirmation |
| `availability_date` | Estimated between Thu, Oct 22 and Sat, Oct 24 | Delivery estimate, not scrape time |
| `product_specifications` | Named specifications and their values | Preserve the stated specifications and their meanings |
| Seller description, shipping, returns, images, and remaining fields | Present in the original payload | Retain alongside the mapped information |

Source evidence: [the saved eBay record](https://github.com/MooseGooseConsulting/techdeals-v2/blob/4d7637c7b3a1e642737c6892c564f01b6ec33f37/corpus/fixture-zero/raw/127932828201.json).
This historical capture demonstrates available fields, not current stock or price.

Ingestion should:

1. Retain the original record, source identifiers, and collection metadata.
2. Map explicit fields directly and normalize their representation where the
   meaning is established. Preserve original values alongside normalization.
3. Interpret messy titles, descriptions, specification tables, or mixed entries
   where needed, retaining the source context and association with the input.
4. Expose separate items or variants when the source contains them, retaining
   which original record they came from and which price describes which entry.
5. Apply the listing identity and rescrape rules below before publishing the
   information for agent queries.

## Initial parser output target

The parser needs a provisional structured-output schema before the first model
call. The target is extracted information from the supplied content, checked
against varied real inputs. It is not the final database schema or a complete
hardware taxonomy. The following is the proposed minimum shape; exact field
definitions and example records come from inspection of the initial captures.

| Part of the output | Information to represent |
|---|---|
| Input references | Which supplied captures and entries support the result; native source IDs and URLs when present |
| Items or variants | An array of extracted entries, with title/description, their source grouping, and any explicit variant distinctions |
| Common item fields | Seller, location, condition, and availability, preserving original values and explicit unknowns |
| Prices | An array of amounts with currency, original text, stated kind such as ask or bid, any source-stated item or listing context, and supporting source references |
| Attributes | Additional specifications and source fields with their original names/values, normalized values and units where justified, and supporting references; different hardware types can have different attributes |
| Source dates | Dates stated in the supplied content, with their meaning, original text, and source references; unresolved meanings remain explicit |
| Unresolved information | Ambiguous fields or associations, conflicts, and entries that could not be interpreted, retaining the relevant evidence |

The result can contain zero, one, or many extracted entries. An empty item
array needs a disposition, such as no offer present or interpretation failed;
it must not silently count as a successful ingestion. A component mentioned
inside a system must not become a separately priced offer unless the source
supports that interpretation. Prices can remain associated with a source group
when their scope cannot be narrowed reliably.

This provisional output target does not prescribe a general purchase-content
relationship model. Preserve source-stated quantities and associations in the
native evidence and extracted result when the inspected inputs support them;
do not infer whether a component is installed, included, or separately
purchasable. Decide whether common relationship fields are useful after the
initial captures and downstream needs have been reviewed. Cost apportionment is
outside the ingestion requirement.

Collection identity and known retrieval times enter with the input metadata.
Import/parse times and parser version are recorded by the program. Carry these
alongside the model result rather than asking the model to generate them.
Returned references must resolve to supplied evidence. Database keys and
listing merges are assigned by the persistence code using established identity
evidence; a model-generated identifier does not establish listing identity.

The first parser executable writes the input context and structured results to
inspectable files. Compare those results with the independently inspected
records before settling the database tables. Refine the output schema when a
real input cannot be represented faithfully. Keep the existing requirements for
dates, identity, and history in view during this experiment so that information
needed for persistence is retained from the start.

## Where LLM parsing fits

The shared LLM-backed parser is the main proposed solution to varied source
layouts and contextual interpretation. It receives captured content with source
and collection context and returns records in a common, extensible structure.
It must handle unfamiliar sources without depending on a handwritten parser
for each one. Structured source information can be copied directly. Keep the
original record available even when interpretation is partial or fails.

Establish whether this approach works with varied real captures, including
sources held back while prompts and preparation are developed. Requiring new
source-specific code to pass every new example would fail the intended design.
Check returned records against the evidence, including missing items, omitted
prices, incorrect associations, invented values, and lost dates. A valid output
structure alone does not establish a correct parse.

Ordinary code validates structure, input references, and known values; performs
deterministic conversions and arithmetic; and applies database identity,
deduplication, and history rules. The model interprets the supplied evidence;
it cannot recover facts that were never captured or establish a missing date.

Batching means multiple entries in each model request. Retry and recovery paths
must preserve that requirement instead of falling back to one request per entry.
Batching reduces the number of requests; it does not eliminate the work of
reading and interpreting the entries supplied in those requests.

Parsed fields need their source reference and parser version. Derived arithmetic
and unit conversions should use the stated inputs and explicit rules. Parsing
the same capture again may revise its interpretation; it must not manufacture
a new market observation. The batch association must survive reordered or
missing model output.

### Interpretation work for each entry

One shared parser means reusing the parsing program and output schema across
different inputs. It does not mean understanding a source's format once removes
the need to interpret every differently written record inside it. For each new
record whose meaning varies from entry to entry, this design requires the LLM
to read and interpret its relevant content. Knowing the surrounding JSON, CSV,
or feed structure does not establish what the individual descriptions mean.

For example, these illustrative descriptions require different interpretations:

- "Four drives, $200 for the lot."
- "Four drives available, $200 each."
- "Server $200, drives pictured but not included."

The wording determines which items are offered and what the price covers.
These are explanatory examples, not real captures or evidence of a working
parser. The model reads entries with the context needed to interpret them;
there is no requirement for one request per literal line or field. A shared
batch can contain many entries, each of which still needs to be interpreted.

Explicit fields with established, consistent meanings can be copied and
normalized by ordinary code. The LLM is used for the information that needs
interpretation. This does not assume a manually written parser for every
possible source, or that all incoming information can bypass the model.

| Incoming information | Required interpretation behavior |
|---|---|
| New heterogeneous entry needing interpretation | Supply its relevant content and context to the LLM, preserving its association with the input |
| Meaningfully changed entry | Re-evaluate the interpretation affected by the change, with enough context to preserve item and price associations |
| Unchanged content and context with a reusable validated interpretation | Reuse that interpretation; a later sighting alone does not require another model call |
| Explicit structured fields with established meanings | Copy and normalize directly where possible; do not require LLM interpretation of those fields |
| Deliberate reprocessing with a revised parser | Produce a separately versioned interpretation of existing evidence, retaining the original observation dates |

Decide reuse before requesting interpretation, using the content and context
the interpretation depends on and its parser version. A missing, failed, or
incompatible prior result is not a successful reusable parse. The
[rescrape rules](#listing-identity-and-repeated-scrapes) still determine identity,
freshness, and history. Reuse for unchanged content does not remove the work
required to interpret genuinely new or meaningfully changed entries.

This is the proposed execution model, not demonstrated functionality. We have
not yet established whether its output is sufficiently complete and correct,
or whether it can process the intended ingestion volume in practice. The first
parser experiment must examine those questions using varied real inputs.

## Date meanings

| Date | Meaning |
|---|---|
| Scraped/retrieved at | When the source was actually retrieved; establishes when a displayed price or stock state was seen |
| Source dates | Publication, source update, auction end, sold date, delivery estimate, or explicitly dated historical quote; each keeps its own meaning |
| Imported at | When the record entered this database |
| Parsed at | When the interpretation was produced |

Importing or parsing an old record today does not make its prices current.
Preserve source timezone and precision when supplied, normalize known instants
consistently, and leave unknown dates unknown. Use established collector
metadata where available. Unfamiliar timestamp fields can be retained with
unresolved meaning; intake does not wait for a custom mapping. Confirm their
meaning before publishing them as observation dates. An explicitly dated
historical quote also keeps its quote date separately from the date we
retrieved the page.

## Listing identity and repeated scrapes

Specify these behaviors before finalizing tables or the scraper output. The
distinctions below describe required behavior, not a prescribed table count.

- **Listing identity:** use a stable source/native listing identifier, with
  variant or other source context where it distinguishes separately priced
  entries. Price and scrape time do not define the listing's identity. A URL
  fallback must preserve meaningful variant parameters. Use established source
  keys or URL rules where available. If reliable identity is unresolved, retain
  the input and parsed information with that status; do not guess a listing
  merge. Targeted source-specific confirmation may be needed before claiming
  reliable listing deduplication. Matching the same hardware model across
  sellers is a separate task and must not collapse their listings.
- **Scrape identity:** identify the collection and its record independently of
  the listing. Reimporting or retrying the same captured result is idempotent.
- **Stored content:** reuse unchanged listing information and retain when it
  was seen. Choose whether compact sightings or first/last-seen intervals meet
  the required history; do not require another complete listing copy on each
  check. Keep source evidence retrievable. A fingerprint used for content reuse
  must exclude changing collection metadata such as run IDs and scrape times;
  it must not discard meaningful item, price, location, or other source fields.
- **Dates:** map each scraper's actual date fields to their established meanings.
  The inspected eBay record `127932828201` contains
  `timestamp: 2026-10-06T02:22:34.880Z`; preserve that provider timestamp and
  confirm its semantics before treating it as the retrieval time. Delivery
  estimates and listing update dates remain separate. New collections should
  provide explicit retrieval times; importing an old result does not refresh it.
- **Agent queries:** expose a clear current-listing read path with one result
  per source listing/variant, its observation date, and freshness. Expose dated
  changes separately. Counting scrapes must not inflate item counts or the
  number of independent price examples. Late-arriving old scrapes must not
  overwrite newer source state simply because they were imported later.

| Case | Required behavior |
|---|---|
| Same captured result imported twice, including a retry | No duplicate listing, state, or sighting |
| Same source listing discovered by overlapping searches or different collectors | Recognize its source listing identity; do not create another item because the discovery route differs |
| New scrape, same listing information | Same listing and content; retain the later successful sighting and freshness |
| New scrape, price changes | Same listing; preserve old and new amounts with their actual observation times |
| Price returns to an earlier amount | Preserve the chronological change back; content reuse must not erase it |
| Stock, location, specifications, or other fields change while price stays the same | Preserve the changed source information without claiming the price changed |
| Same model offered by different sellers | Distinct source listings; no identity merge based on model alone |
| Old scrape arrives after a newer one | Preserve history without regressing the current known state |
| Parser reruns against existing evidence | Retain revised interpretation separately; no fabricated new scrape or observation date |

Verify the deduplication keys and database constraints with repeated and
concurrent imports. An incomplete scrape must have an explicit disposition; a
missing field does not by itself establish a real deletion or stock change.

## Scraper output is part of the design

Inspect what existing collectors supply to the shared parser.
Where necessary fields or time/identity semantics are missing, consider a
targeted collector change. A custom Bright Data scraper is an available option
for suitable sources; other sources can keep their own acquisition methods.
Existing output formats can be improved. Bright Data's [Scraper Studio](https://docs.brightdata.com/datasets/scraper-studio/faqs)
supports building custom scrapers.

Specify stable listing and variant IDs where supplied by the site, source URL,
per-record retrieval time, collection/record identity, and the complete useful
scraped fields. Keep collection metadata separate from listing content so that
deduplication can distinguish a new visit from a changed item. Retain original
text and source-specific fields alongside normalized values. LLM parsing is for
interpretation the source output does not already provide directly.

Choosing or changing a collector is proposed work. No scraper, paid collection,
or production schedule has been created or changed for this spike.

## Choices still to resolve

- Which initial examples have established listing/variant keys and dates, and which remain explicitly unresolved; targeted fallbacks where required for reliable history or deduplication.
- The meaning and precision of existing provider timestamps.
- Which source fields participate in content comparison, and how normalization affects it.
- Whether compact sightings or first/last-seen intervals preserve the history we need.
- The actual stored records and parser output shape, checked against the selected source data.
