#!/usr/bin/env python3
"""Rebuild the bounded corpus from pinned source bytes, never parser output.

Requires the two source checkouts and local archive; no network or model calls.
Every selected Handoff file is checked against the pinned git object first.
"""

import argparse
import csv
import hashlib
import io
import json
import re
import subprocess
import zipfile
from pathlib import Path

HANDOFF_COMMIT = "1ac8f887db62420a2c2dc2de2ed41597ee7802c6"
WALK_COMMIT = "4d7637c7b3a1e642737c6892c564f01b6ec33f37"
MEMBER = "raw_source/raw_variations.json"
MEMBER_SHA = "2683948c318287c7f3b199f951e750e7e8eb94b699e4ce1340bffb2111779309"
CENTRAL_SHA = "8ccc658e87692fef14d18a4bb53acbdb4235aa911d2933cce4151303d6bc2c8a"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--handoff", type=Path, default=Path("/home/coldaine/TechdealsHandoff"))
    ap.add_argument(
        "--walkthrough", type=Path, default=Path("/home/coldaine/techdeals-v2-walkthrough")
    )
    ap.add_argument(
        "--archive",
        type=Path,
        default=Path("/home/coldaine/Downloads/fresh-512gb-server-discovery-2026-09-13.zip"),
    )
    ap.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1])
    args = ap.parse_args()
    manifest = {
        "version": 1,
        "packages": {k: [] for k in ("dev-a", "dev-b", "holdout-c")},
        "captures": [],
        "oracle_path": "corpus/oracle/expected.json",
    }

    def pinned(root, commit, rel):
        data = (root / rel).read_bytes()
        expected = subprocess.check_output(["git", "-C", str(root), "show", f"{commit}:{rel}"])
        if data != expected:
            raise ValueError(f"Working copy differs from pinned source: {rel}")
        return data

    def add(
        package,
        ident,
        data,
        media,
        namespace,
        origin,
        commit,
        classification="native_capture",
        selection="entire file, exact original bytes",
        linked=None,
        parent=None,
    ):
        ext = {
            "application/json": "json",
            "text/html": "html",
            "application/atom+xml": "xml",
            "text/csv": "csv",
        }[media]
        rel = f"corpus/native/{package}/{ident}.{ext}"
        out = args.output / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        provenance = {
            "classification": classification,
            "origin_path": origin,
            "source_commit": commit,
            "selection_rule": selection,
        }
        if parent:
            provenance["parent_sha256"] = parent
        row = {
            "id": ident,
            "path": rel,
            "media_type": media,
            "acquisition_key": f"frozen:{ident}",
            "source_namespace": namespace,
            "metadata": {
                "retrieved_at": None,
                "acquisition_route": "unverified historical capture",
            },
            "sha256": sha(data),
            "provenance": provenance,
        }
        if linked:
            row["linked_group"] = linked
        manifest["captures"].append(row)
        manifest["packages"][package].append(ident)

    rel = "corpus/fixture-zero/raw/127932828201.json"
    add(
        "dev-a",
        "ebay-bundle",
        pinned(args.walkthrough, WALK_COMMIT, rel),
        "application/json",
        "ebay",
        rel,
        WALK_COMMIT,
    )
    fixtures = [
        (
            "dev-a",
            "spd",
            "serverpartdeals/fixtures/collections_all_p1.json",
            "application/json",
            "serverpartdeals",
            "native_capture",
            None,
        ),
        (
            "dev-a",
            "hls",
            "reddit_homelabsales/fixtures/sample_atom.xml",
            "application/atom+xml",
            "reddit",
            "authored_fixture",
            None,
        ),
        (
            "dev-a",
            "gov-asset",
            "govdeals/fixtures/asset_30467_94.json",
            "application/json",
            "govdeals",
            "authored_enriched_fixture",
            "govdeals:30467:94:1",
        ),
        (
            "dev-a",
            "gov-bid",
            "govdeals/fixtures/bidbox_30467_94.json",
            "application/json",
            "govdeals",
            "authored_enriched_fixture",
            "govdeals:30467:94:1",
        ),
        (
            "dev-b",
            "disctech",
            "disctech/fixtures/ssd_listing_p1.html",
            "text/html",
            "disctech",
            "native_capture",
            None,
        ),
        (
            "holdout-c",
            "hws",
            "reddit_homelabsales/fixtures/sample_hws_atom.xml",
            "application/atom+xml",
            "reddit",
            "authored_fixture",
            None,
        ),
    ]
    for package, ident, p, media, namespace, kind, linked in fixtures:
        rel = "scripts/ingest/" + p
        add(
            package,
            ident,
            pinned(args.handoff, HANDOFF_COMMIT, rel),
            media,
            namespace,
            rel,
            HANDOFF_COMMIT,
            kind,
            linked=linked,
        )
    with zipfile.ZipFile(args.archive) as archive:
        data = archive.read(MEMBER)
    assert sha(data) == MEMBER_SHA
    records = json.loads(data)
    for package, indices in [("dev-a", [1, 8, 10, 16]), ("holdout-c", [6, 14, 17, 18])]:
        for index in indices:
            record = records[index]
            add(
                package,
                f"archive-{index}",
                (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode(),
                "application/json",
                "ebay",
                args.archive.name + "!" + MEMBER,
                None,
                "parsed_raw_fragment",
                f"zero-based JSON array index {index}; JSON reserialized, not original bytes",
                linked="ebay:" + record["product_id"],
                parent=MEMBER_SHA,
            )
    rel = "research/ssd-pricing-brightdata/ssd_prices_export_v2.csv"
    data = pinned(args.handoff, HANDOFF_COMMIT, rel)
    # csv.reader handles quoted multiline records; preserve the original prefix bytes.
    stream = io.StringIO(data.decode(), newline="")
    reader = csv.reader(stream)
    for _ in range(11):
        next(reader)
    selected = stream.getvalue()[: stream.tell()].encode()
    add(
        "dev-a",
        "ssd-export",
        selected,
        "text/csv",
        "google-shopping-export",
        rel,
        HANDOFF_COMMIT,
        "derived_export",
        "header and first 10 CSV records; exact original prefix bytes",
        parent=sha(data),
    )
    rel = "scripts/ingest/central_computers/fixtures/all_products_p1_96.html"
    data = pinned(args.handoff, HANDOFF_COMMIT, rel)
    assert sha(data) == CENTRAL_SHA
    text = data.decode()
    cards = []
    for ident in ("452449", "452448", "449525"):
        at = text.index('id="product-item-info_' + ident + '"')
        start = text.rfind('<li class="item product product-item">', 0, at)
        # Cards contain nested store inventory lists: use matching li depth.
        depth = 0
        for match in re.finditer(r"</?li\b[^>]*>", text[start:]):
            depth += -1 if match.group().startswith("</") else 1
            if depth == 0:
                end = start + match.end()
                break
        cards.append(text[start:end])
    # Preserve shared head/context and disclaimers, without unrelated card contents.
    head = re.search(r"<head\b.*?</head>", text, re.DOTALL).group()
    disclaimers = []
    for match in re.finditer(r"[^<>\n]*(?:inventory|Inventory)[^<>\n]*", text):
        value = match.group().strip()
        if len(value) > 60 and ("accur" in value.lower() or "guarantee" in value.lower()):
            disclaimers.append(value)
    fragment = (
        "<html>"
        + head
        + "<body><ol>"
        + "".join(cards)
        + "</ol><aside>"
        + "\n".join(dict.fromkeys(disclaimers))
        + "</aside></body></html>\n"
    )
    add(
        "holdout-c",
        "central-selected",
        fragment.encode(),
        "text/html",
        "central-computers",
        rel,
        HANDOFF_COMMIT,
        "selected_native_html_fragment",
        "exact enclosing li.product-item bytes for IDs 452449,452448,449525; original head and shared inventory disclaimer text; excludes other 93 product cards",
        parent=CENTRAL_SHA,
    )
    # Initial two-record experiment: retain complete native objects/cards only.
    manifest["packages"]["small-a"] = []
    rel = "scripts/ingest/serverpartdeals/fixtures/collections_all_p1.json"
    spd_data = pinned(args.handoff, HANDOFF_COMMIT, rel)
    product = json.loads(spd_data)["products"][0]
    assert product["id"] == 8297455845654
    add(
        "small-a",
        "small-spd",
        (json.dumps(product, ensure_ascii=False, indent=2) + "\n").encode(),
        "application/json",
        "serverpartdeals",
        rel,
        HANDOFF_COMMIT,
        "parsed_raw_fragment",
        "complete products[0] object (id 8297455845654); JSON reserialized, not original bytes",
        parent=sha(spd_data),
    )
    rel = "scripts/ingest/disctech/fixtures/ssd_listing_p1.html"
    html_data = pinned(args.handoff, HANDOFF_COMMIT, rel)
    start = html_data.index(b'<div class="facets-item-cell-list"')
    depth = 0
    for match in re.finditer(rb"</?div\b[^>]*>", html_data[start:]):
        depth += -1 if match.group().startswith(b"</") else 1
        if depth == 0:
            end = start + match.end()
            break
    card = html_data[start:end]
    assert b'data-item-id="17037"' in card
    native_ld = json.loads(
        re.search(
            rb"<script[^>]*application/ld\+json[^>]*>(.*?)</script>",
            html_data,
            re.DOTALL,
        ).group(1)
    )
    selected_product = native_ld["@graph"][0]["itemListElement"][0]["item"]
    assert selected_product["sku"] == "DEL-345-BHSC-BN-OE"
    # Context URI is copied from the original script; base is the native seller URI.
    selected_ld = {"@context": native_ld["@context"], **selected_product}
    small_html = (
        b'<html><head><base href="https://www.disctech.com/">'
        b'<script type="application/ld+json">'
        + json.dumps(selected_ld, ensure_ascii=False, indent=2).encode()
        + b"</script></head><body>"
        + card
        + b"</body></html>\n"
    )
    add(
        "small-a",
        "small-disctech",
        small_html,
        "text/html",
        "disctech",
        rel,
        HANDOFF_COMMIT,
        "selected_native_html_fragment_with_reserialized_jsonld",
        "exact first enclosing div.facets-item-cell-list bytes (data-item-id 17037, SKU DEL-345-BHSC-BN-OE); complete matching native JSON-LD Product selected and reserialized with original @context; base resolves native relative URLs; excludes other 23 products",
        parent=sha(html_data),
    )
    (args.output / "corpus/manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
