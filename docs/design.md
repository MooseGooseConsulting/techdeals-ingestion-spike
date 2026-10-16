# Scraped source to useful database records

Design correction, 2026-10-16. This is the next implementation target. The
current executable still uses the larger prototype contract described below;
this document does not claim that the replacement has been implemented.

## The job

Accept heterogeneous material from existing scrapers, identify its entries, and
store the items, all stated prices, location, seller, condition, availability,
specifications and dates in PostgreSQL for other agents to query. Preserve the
original material and collection context. One source can produce many records;
known linked responses can describe one record. Different collection routes do
not require separate semantic parsers.

## What the prototype designed instead

The prototype asks the model to build a graph: batch -> units -> offers -> items
and prices, with model-created local IDs, price scope references, arbitrary
relationships, identity claims, and evidence wrappers on individual facts.
It also contains two parser strategies, discovery/read tools, experiment arms,
and interpretation selection machinery. These were built before useful extraction
was demonstrated. Most recently the direct path bypassed discovery/tools but
still required the same graph output.

This makes the model perform application bookkeeping as well as extraction.
A useful price can be rejected because an invented graph ID points at the wrong
node. A schema-valid graph can still contain an invented currency or omit a
stock qualification. Repairing those mechanisms does not establish that the
resulting records are useful.

## The model's output: entries with named fields

Return a list of extracted entries. Each entry represents a separately listed
item, selectable configuration, or whole bundle/lot. A source containing ten
separate items can produce ten entries. A bundle with one stated ask has one
entry with that ask; its description and structured attributes preserve contents.
There is no requirement to model every mentioned component as a graph node.

| Field | Meaning |
|---|---|
| source_refs | One or more supplied input references, with optional native entry locators, identifying the material supporting this entry; never model-invented database keys |
| title, description | Item/configuration/bundle text; preserve original source strings |
| native_id, variant_id, sku, url | Native identifiers and links, separately named when present |
| seller, location, condition, availability | Nullable source-supported values; vendor is distinct from seller; qualifications remain available |
| prices | All stated prices for this entry, each with decimal amount or unknown, currency or unknown, original text, kind and any stated condition/basis |
| source_dates | Native date fields with their original names, object scope, original values, and meanings where established |
| attributes | Flexible structured specifications and remaining useful native fields; values can include lists/objects |
| issues | Explicit ambiguities, conflicts, source truncation, or material information not interpreted |

Common values are ordinary nullable typed fields, rather than seven-property
Fact wrappers. Prices are nested under the entry they describe, so associating a
price does not require the model to invent IDs and reference another node.
Multiple prices retain labels such as ask, bid, previous price or shipping.
If their association is ambiguous, preserve that ambiguity and original text.
Do not assign a bundle's ask to its included parts or infer stock from a
purchase-quantity selector. Store source quantities when stated; invent none.

The output can retain native vendor, images, warranty, nested options and other
fields without requiring every source to have identical hardware columns.
Raw preservation also keeps unmapped information available; it does not pretend
that information has already become a normalized cross-source field.

## What the application owns

- Assign capture IDs, database IDs and processing status. The model only selects
  supplied input references and extracts source identifiers. An entry extracted
  from known linked responses retains references to all supporting inputs.
- Retain exact source bytes and collection metadata before parsing. Attach the
  capture link to every stored entry. Model-provided locators must resolve.
- Carry trusted retrieval time from the collector; keep it unknown when absent.
  Record import/parse time separately. The model extracts source dates and never
  manufactures observation time.
- Validate types, input references, decimal values and structural associations.
  Use PydanticAI's normal bounded correction within the same conversation.
- Persist extracted entries and their nested prices as queryable data. JSONB is
  appropriate for variable native attributes; retaining an opaque response alone
  does not satisfy the database requirement.
- Handle retries, unchanged-content reuse, listing identity and dated changes.
  Several entries at one URL are distinct. Unknown identity remains unresolved;
  neither a model-generated key nor a URL alone proves cross-scrape equivalence.
- Retain failed attempts and original inputs, with a direct explanation of why
  output was not accepted. A schema pass is not a factual-completeness claim.

The existing Neon connection, credential injection, raw retention and SDK message
capture can be reused where they fit. The prototype's graph schema, table layout,
experiment arms and acceptance selectors do not define the replacement design.

## The initial execution path

Retain native inputs and metadata -> give a small varied batch to one PydanticAI
extraction agent -> validate its entry output -> write entries/prices to Neon ->
SELECT the stored fields and compare them directly with the original inputs.

Keep the source readable in the prompt. Do not JSON-stringify an already
serialized JSON or HTML payload inside another JSON string as the presentation
format; that adds escaping and makes faithful text copying harder. Retain exact
bytes separately and present labelled native source blocks or actual JSON values.

Use the existing free-model configuration with reasoning enabled and Doppler
credentials. Model availability and suitability still need live proof. Do not
turn provider failures or bad model output into success by weakening checks.
Capture requests, responses and correction messages for inspection.

Start with the two existing native examples, then a real source containing
multiple separately priced entries. For each, inspect item/price association,
unknown currency/seller/location, source dates versus retrieval time, and retained
qualifications. Follow with reingestion of the same capture, an unchanged later
scrape, and a changed-price scrape. Source truth is checked before expansion.

Large-input preparation is a later demonstrated need: batch complete native
entries with shared context when possible. A document that exceeds the chosen
model's actual usable limits needs an explicit preparation decision. Do not make
discovery agents, a retrieval protocol, or a benchmark comparison prerequisites
for the small extraction path.

## What is separate from this implementation

Model-generated database graphs, general relationship predicates, component cost
allocation, scoring, matching/ranking engines, competing parser arms, evaluator
framework development and production migration are not first-delivery tasks.
Useful source associations, faithful prices, dates and rescrape behavior remain
required. Consumer agents can perform their own subsequent analysis.
