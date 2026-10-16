"""Frozen evidence integrity and boundaries, independent of parser behavior."""

import csv
import hashlib
import io
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def manifest():
    return json.loads((ROOT / "corpus/manifest.json").read_text())


def test_capture_hashes_and_oracle_boundary():
    data = manifest()
    captures = {c["id"]: c for c in data["captures"]}
    ids = [ident for package in data["packages"].values() for ident in package]
    assert len(ids) == len(set(ids)) == len(captures) == 17
    assert set(ids) == set(captures)
    for capture in captures.values():
        path = capture["path"]
        assert path.startswith("corpus/native/")
        assert data["oracle_path"] != path
        assert capture["metadata"]["retrieved_at"] is None
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == capture["sha256"]
        assert capture["provenance"]["selection_rule"]


def test_selected_holdout_has_only_three_complete_cards_and_inventory():
    text = (ROOT / "corpus/native/holdout-c/central-selected.html").read_text()
    assert re.findall(r'id="product-item-info_(\d+)"', text) == ["452449", "452448", "449525"]
    assert text.count('class="item product product-item"') == 3
    assert len(re.findall(r'<div class="instock">', text)) == 15
    assert "Disclaimer: Inventory accuracy is not guaranteed." in text
    assert "<head" in text and "</head>" in text


def test_feed_occurrences_and_derived_export_are_preserved():
    ns = {"a": "http://www.w3.org/2005/Atom"}
    feed = ET.parse(ROOT / "corpus/native/holdout-c/hws.xml")
    ids = [e.find("a:id", ns).text for e in feed.findall("a:entry", ns)]
    assert len(ids) == 5 and len(set(ids)) == 4
    rows = list(
        csv.DictReader(io.StringIO((ROOT / "corpus/native/dev-a/ssd-export.csv").read_text()))
    )
    assert [r["obs_id"] for r in rows] == [
        "192",
        "206",
        "196",
        "194",
        "145",
        "179",
        "160",
        "156",
        "146",
        "209",
    ]
    assert rows[6]["capacity_gb"] == "2000" and "1TB" in rows[6]["model_name"]


def test_rebuild_byte_reproducible(tmp_path):
    sources = [
        Path("/home/coldaine/TechdealsHandoff"),
        Path("/home/coldaine/techdeals-v2-walkthrough"),
        Path("/home/coldaine/Downloads/fresh-512gb-server-discovery-2026-09-13.zip"),
    ]
    if not all(path.exists() for path in sources):
        pytest.skip(
            "Optional regeneration requires the pinned source checkouts and original archive"
        )
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/freeze_corpus.py"), "--output", str(tmp_path)],
        check=True,
    )
    data = manifest()
    assert (tmp_path / "corpus/manifest.json").read_bytes() == (
        ROOT / "corpus/manifest.json"
    ).read_bytes()
    for capture in data["captures"]:
        assert (tmp_path / capture["path"]).read_bytes() == (ROOT / capture["path"]).read_bytes()
