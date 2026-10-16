import json

import pytest

from techdeals_ingestion.sources import load_files, load_manifest


def test_loads_exact_native_bytes_and_explicit_context(tmp_path):
    payload = b'{"native": [1, 2]}\n'
    path = tmp_path / "source.json"
    path.write_bytes(payload)
    package = load_files(
        [path],
        namespace="seller-x",
        url="https://example.test/list",
        retrieved_at="2026-10-16T12:30:00-05:00",
        metadata={"batch": "run-1"},
    )
    source = package.sources[0]
    assert source.content == payload
    assert source.media_type == "application/json"
    assert source.namespace == "seller-x"
    assert source.url == "https://example.test/list"
    assert source.retrieved_at.isoformat() == "2026-10-16T12:30:00-05:00"
    assert source.metadata == {"batch": "run-1", "filename": "source.json"}


def test_retrieval_time_must_be_timezone_aware(tmp_path):
    path = tmp_path / "source.txt"
    path.write_text("raw")
    with pytest.raises(ValueError, match="explicit timezone"):
        load_files([path], namespace="seller-x", retrieved_at="2026-10-16T12:30:00")


def test_explicit_acquisition_key_and_retrieval_receipts_are_stable(tmp_path):
    path = tmp_path / "source.txt"
    path.write_text("raw")
    first = load_files([path], namespace="seller-x", acquisition_key="collector:batch-1")
    second = load_files([path], namespace="seller-x", acquisition_key="collector:batch-1")
    observed = load_files([path], namespace="seller-x", retrieved_at="2026-10-16T12:30:00Z")
    later = load_files([path], namespace="seller-x", retrieved_at="2026-10-17T12:30:00Z")
    assert first.sources[0].acquisition_key == "collector:batch-1"
    assert second.sources[0].acquisition_key == first.sources[0].acquisition_key
    assert observed.sources[0].acquisition_key != later.sources[0].acquisition_key


def test_rescraped_file_gets_stable_acquisition_specific_input_ids(tmp_path):
    path = tmp_path / "same.json"
    path.write_text('{"item": 1}')
    first = load_files([path], namespace="seller-x", retrieved_at="2026-10-16T12:30:00Z")
    same_receipt = load_files([path], namespace="seller-x", retrieved_at="2026-10-16T12:30:00Z")
    later_receipt = load_files([path], namespace="seller-x", retrieved_at="2026-10-17T12:30:00Z")
    assert first.sources[0].input_id == same_receipt.sources[0].input_id
    assert first.sources[0].input_id != later_receipt.sources[0].input_id
    assert first.sources[0].metadata["filename"] == path.name


def test_manifest_package_uses_native_files_and_keeps_unknown_time_unknown():
    package = load_manifest("corpus/manifest.json", "small-a")
    assert [source.input_id for source in package.sources] == ["small-spd", "small-disctech"]
    assert all(source.content for source in package.sources)
    assert all(source.retrieved_at is None for source in package.sources)
    assert all("oracle" not in str(source.metadata).lower() for source in package.sources)


def test_manifest_rejects_paths_outside_native_corpus(tmp_path):
    repo = tmp_path / "repo"
    corpus = repo / "corpus"
    (corpus / "native").mkdir(parents=True)
    (corpus / "oracle").mkdir()
    (corpus / "oracle" / "expected.json").write_text("{}")
    manifest = corpus / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "packages": {"unsafe": ["oracle"]},
                "captures": [
                    {"id": "oracle", "path": "corpus/oracle/expected.json", "acquisition_key": "x"}
                ],
            }
        )
    )
    with pytest.raises(ValueError, match="outside corpus/native"):
        load_manifest(manifest, "unsafe")
