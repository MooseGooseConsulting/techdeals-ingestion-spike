# Frozen evidence packages

`manifest.json` is the complete parser-input allowlist. Paths are relative to the
repository root. `dev-a` has ten captures, `dev-b` one complete HTML listing,
and `holdout-c` six captures. Captures with the same `linked_group` jointly
describe one listing. Acquisition keys identify saved payload occurrences;
source namespaces identify the originating listing system separately.

`oracle/expected.json` is evaluator-only. It contains independently inspected
semantic assertions, source locators, expected offer counts, prices and scopes,
conflicts, unknowns, source-date meanings, and forbidden inferences. It must
never enter a prompt, preparation context, or parser input allowlist. It is not
expected parser JSON and was not derived from parser results. Native JSON-LD
and catalog values were read directly as evidence; CSV normalization remains
an export claim, not independent truth. Assertion omissions do not authorize
invented fields. Changes after holdout evaluation make these examples development
material and require a new holdout before claiming unfamiliar-source performance.

All trusted retrieval dates are null. Native timestamp fields remain in the
payload; naming a field `timestamp`, `updated`, or `scraped_at` does not establish
actual retrieval time. No mtime, archive date, import time, or current date is
substituted. Historical stock and price are not current observations.

The authored social feeds test multiple items, buyer requests, trades, bundles,
and duplicate occurrences. They do not prove a real social source works. The
linked auction fixtures include authored/enriched fields and do not prove a
real split-response acquisition. A verified non-Bright-Data acquisition route,
real multi-item post, real split auction responses, and confirmed acquisition
provenance remain explicit coverage gaps.

Rebuild with `python scripts/freeze_corpus.py`; options select source checkouts,
archive and output directory. The script checks each selected source file against
its pinned git object, hashes the entire archive member and Central page, copies
whole files byte-for-byte, and deterministically selects fragments. Archive JSON
records are reserialized parsed raw fragments, explicitly not original bytes.
The CSV prefix preserves the header and first ten complete CSV records. Central
preserves the exact three enclosing product-card byte ranges, original head and
shared inventory disclaimer; the other 93 cards are absent from parser access.
Manifest provenance records parent hashes, source commits, paths, selection rules,
and classifications. No request to a collector or model is made during freezing.

`python -m pytest tests/test_corpus.py` checks hashes, package boundaries,
occurrence retention, selected-card isolation and a byte-for-byte rebuild.
Rebuilding requires the pinned local source checkouts and ZIP; ordinary corpus
consumption requires only this checkout.

## Initial small experiment

`small-a` contains two complete native records in different formats: the first
ServerPartDeals product object and the first DiscTech HTML product card with its
matching complete native JSON-LD Product. The JSON object is reserialized; the
HTML card bytes are exact, and the selected JSON-LD object is reserialized.
The generated HTML wrapper/base is documented selection context. No other
products are included. Each fragment retains its parent hash and pinned full
source path. Existing 17 captures and package partitions remain unchanged.
The total allowlist now has 19 captures, including these two small fragments.
Independent evaluator-only meanings are in `small-spd` and `small-disctech`
oracle cases; these remain excluded from model input.
