import json

import pytest

from techdeals_ingestion.contracts import CaptureInput, EvidenceRef
from techdeals_ingestion.evidence import EvidenceIndex, load_work_packages


def test_native_views_and_originals(tmp_path):
    payloads = [
        ("j", "application/json", b'{"a/b":{"~x":19},"empty":null}'),
        (
            "h",
            "text/html",
            b'<html><body><p hidden data-price="12">hidden</p><script type="application/ld+json">{"price":12}</script></body></html>',
        ),
        (
            "x",
            "application/xml",
            b'<feed><item price="3">a</item><item>b</item></feed>',
        ),
        ("c", "text/csv", b'name,price\n"a,b",23\n'),
    ]
    captures = []
    for cid, media, raw in payloads:
        p = tmp_path / cid
        p.write_bytes(raw)
        captures.append(CaptureInput(capture_id=cid, acquisition_key=cid, path=p, media_type=media))
    index = EvidenceIndex(captures)
    assert index.originals == {cid: raw for cid, _, raw in payloads}
    assert index.resolve(EvidenceRef(capture_id="j", locator="json:/a~1b/~0x")) == "19"
    assert 'data-price="12"' in index.resolve(
        EvidenceRef(capture_id="h", locator="html:/html[1]/body[1]/p[1]")
    )
    assert index.search("ld+json", ["h"])["total"] > 0
    assert 'price="3"' in index.resolve(EvidenceRef(capture_id="x", locator="xml:/feed[1]/item[1]"))
    assert json.loads(index.resolve(EvidenceRef(capture_id="c", locator="csv:/rows/1"))) == [
        "a,b",
        "23",
    ]
    assert (
        len(index.list_children(EvidenceRef(capture_id="x", locator="xml:/feed[1]"))["refs"]) == 2
    )
    with pytest.raises(KeyError):
        index.resolve(EvidenceRef(capture_id="j", locator="json:/missing"))


def test_utf8_pagination(tmp_path):
    p = tmp_path / "text"
    p.write_text("é" * 10000)
    index = EvidenceIndex(
        [CaptureInput(capture_id="t", acquisition_key="t", path=p, media_type="text/plain")]
    )
    ref = EvidenceRef(capture_id="t", locator="$")
    offset = 0
    pages = []
    while True:
        page = index.read(ref, offset=offset, limit=16383)
        assert len(page["text"].encode()) <= 16383
        pages.append(page["text"])
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert "".join(pages) == p.read_text()


def test_manifest_loader_does_not_read_oracle(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    manifest = {
        "packages": {"dev": ["a"]},
        "captures": [
            {
                "id": "a",
                "path": "corpus/a.json",
                "media_type": "application/json",
                "acquisition_key": "a",
                "metadata": {"retrieved_at": None},
            }
        ],
        "oracle_path": "missing",
    }
    p = corpus / "manifest.json"
    p.write_text(json.dumps(manifest))
    package = load_work_packages(p)[0]
    assert package.captures[0].path == corpus / "a.json"
    assert package.captures[0].retrieved_at is None


def test_html_aliases_are_short_and_traversable(tmp_path):
    p = tmp_path / "deep.html"
    p.write_text("<div>" * 50 + '<span hidden="yes">value</span>' + "</div>" * 50)
    index = EvidenceIndex(
        [CaptureInput(capture_id="h", acquisition_key="h", path=p, media_type="text/html")]
    )
    refs = index.snapshot()[0]["refs"]
    assert all(len(ref["locator"]) < 12 for ref in refs)
    assert (
        index.list_children(EvidenceRef(capture_id="h", locator="$"))["refs"][0]["locator"]
        == "html:@1"
    )
    assert (
        index.list_children(EvidenceRef(capture_id="h", locator="html:/div[1]"))["refs"][0][
            "locator"
        ]
        == "html:@2"
    )
    assert 'hidden="yes"' in index.resolve(EvidenceRef(capture_id="h", locator="html:@51"))


def test_changed_capture_cannot_claim_the_original_manifest_hash(tmp_path):
    import hashlib

    source = tmp_path / "capture"
    source.write_bytes(b"original")
    capture = CaptureInput(
        capture_id="capture",
        acquisition_key="capture",
        path=source,
        media_type="text/plain",
        metadata={"sha256": hashlib.sha256(b"original").hexdigest()},
    )
    assert EvidenceIndex([capture]).originals["capture"] == b"original"
    source.write_bytes(b"modified")
    with pytest.raises(ValueError, match="declared checksum"):
        EvidenceIndex([capture])
