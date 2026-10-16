# Scraped records, dates, parsing, and deduplication

Status: draft design, 2026-10-16. These are the data behaviors to establish in
the spike. Database tables and implementation have not been created.

The [problems document](problems.md) explains what needs solving. This document
describes what scraped information means and how ingestion should handle it.
The [plan](plan.md) sets the implementation sequence.

## What comes in

Retain the scraped item, prices, currency, location, seller, condition,
availability, description, specifications, dates, and other source fields.
Preserve the complete original record and its collection context. A complete
component interpretation is not required before this information can be stored.

Different sources may return one listing, a catalog with many products, several
variants, or a post containing multiple items. Preserve the original source
record while exposing the useful individual entries and their associations.

## From a source record to stored information

The inspected eBay capture for listing `127932828201` illustrates the mapping.
These are example meanings, not final database column names:

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

## Where LLM parsing fits

Use batched free-model interpretation for entries or fields whose meaning needs
context. Structured source information can be mapped directly. Keep the original
record available even when interpretation is partial or fails.

Parsed fields need their source reference and parser version. Derived arithmetic
and unit conversions should use the stated inputs and explicit rules. Parsing
the same capture again may revise its interpretation; it must not manufacture
a new market observation. The batch association must survive reordered or
missing model output.

## Date meanings

| Date | Meaning |
|---|---|
| Scraped/retrieved at | When the source was actually retrieved; establishes when a displayed price or stock state was seen |
| Source dates | Publication, source update, auction end, sold date, delivery estimate, or explicitly dated historical quote; each keeps its own meaning |
| Imported at | When the record entered this database |
| Parsed at | When the interpretation was produced |

Importing or parsing an old record today does not make its prices current.
Preserve source timezone and precision when supplied, normalize known instants
consistently, and leave unknown dates unknown. Each collector needs a documented
mapping for its actual timestamp fields. An explicitly dated historical quote
also keeps its quote date separately from the date we retrieved the page.

## Listing identity and repeated scrapes

Specify these behaviors before finalizing tables or the scraper output. The
distinctions below describe required behavior, not a prescribed table count.

- **Listing identity:** use a stable source/native listing identifier, with
  variant or other source context where it distinguishes separately priced
  entries. Price and scrape time do not define the listing's identity. A URL
  fallback needs explicit source-specific rules that preserve meaningful
  variant parameters. Matching the same hardware model across sellers is a
  separate task and must not collapse their listings.
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

Inspect what existing collectors already supply and shape the intake around the
actual source data. Where an output is missing necessary fields or has unclear
time/identity semantics, consider a targeted change or a custom Bright Data
scraper. The owner explicitly identified this option; existing output formats
are not immutable. Bright Data's [Scraper Studio](https://docs.brightdata.com/datasets/scraper-studio/faqs)
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

- Per-source listing and variant keys, including fallbacks where native IDs are missing.
- The meaning and precision of existing provider timestamps.
- Which source fields participate in content comparison, and how normalization affects it.
- Whether compact sightings or first/last-seen intervals preserve the history we need.
- The actual stored records and parser output shape, checked against the selected source data.
