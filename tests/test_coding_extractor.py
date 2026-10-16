import hashlib
import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import coding_extractor as harness


def _fixture_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    native = repo / "corpus" / "native" / "dev-a"
    native.mkdir(parents=True)
    product = {"id": 101, "title": "Original part", "variants": [
        {"id": 201, "product_id": 101, "price": "14.99", "available": True}
    ]}
    (native / "spd.json").write_text(json.dumps({"products": [product]}))
    (repo / "corpus/manifest.json").write_text(json.dumps({
        "packages": {"dev-a": ["spd"]},
        "captures": [{"id": "spd", "path": "corpus/native/dev-a/spd.json",
                      "acquisition_key": "a", "source_namespace": "example",
                      "media_type": "application/json"}],
    }))
    (repo / "corpus/oracle").mkdir()
    (repo / "corpus/oracle/expected.json").write_text(json.dumps({
        "cases": {"spd": {"offers": [{"native_id": "101", "variant_id": "201",
                                        "amount": "14.99"}]}}
    }))
    monkeypatch.setitem(sys.modules, "techdeals_ingestion", types.ModuleType("techdeals_ingestion"))
    models = types.ModuleType("techdeals_ingestion.models")

    class Extraction:
        @staticmethod
        def model_json_schema():
            return {"type": "object", "properties": {"entries": {"type": "array"}}}

        @staticmethod
        def model_validate(data):
            class Parsed:
                def model_dump(self, mode=None):
                    return data
            return Parsed()

        @staticmethod
        def model_validate_json(source):
            return Extraction.model_validate(json.loads(source))

    class Source:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    models.Extraction = Extraction
    models.Source = Source
    parser = types.ModuleType("techdeals_ingestion.parser")
    parser._validate_output = lambda output, sources: []
    monkeypatch.setitem(sys.modules, "techdeals_ingestion.models", models)
    monkeypatch.setitem(sys.modules, "techdeals_ingestion.parser", parser)
    return repo


def _program(workspace):
    (workspace / "extractor.py").write_text('''import argparse, json
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument("--manifest")
p.add_argument("--output")
a = p.parse_args()
manifest = json.loads(Path(a.manifest).read_text())
entries = []
for source in manifest["captures"]:
    doc = json.loads((Path(a.manifest).parent / source["path"]).read_text())
    for i, product in enumerate(doc["products"]):
        for j, variant in enumerate(product["variants"]):
            entries.append({"native_id": str(product["id"]),
                            "variant_id": str(variant["id"]),
                            "source_refs": [{"input_id": source["input_id"],
                                             "locator": f"json:/products/{i}/variants/{j}"}],
                            "prices": [{"kind": "ask", "original": str(variant["price"]),
                                        "amount": str(variant["price"]), "currency": None}]})
Path(a.output).write_text(json.dumps({"entries": entries, "issues": []}))
''')


def test_prepare_preserves_bytes_and_hides_oracle(tmp_path, monkeypatch):
    root = _fixture_repo(tmp_path, monkeypatch)
    workspace = tmp_path / "workspace"
    info = harness.prepare(root, "spd", workspace)
    assert info["captures"] == ["spd"]
    metadata = json.loads((workspace / "captures.json").read_text())["captures"][0]
    assert metadata["sha256"] == hashlib.sha256((workspace / metadata["path"]).read_bytes()).hexdigest()
    assert not (workspace / "corpus").exists()
    assert not (workspace / "oracle").exists()
    assert (workspace / "contract.schema.json").exists()


def test_dev_evaluation_and_hidden_mutation_catch_hardcoding(tmp_path, monkeypatch):
    root = _fixture_repo(tmp_path, monkeypatch)
    workspace = tmp_path / "work"
    harness.prepare(root, "spd", workspace)
    _program(workspace)
    result = harness.evaluate(workspace)
    assert result["passed"], result
    assert result["mutation_passed"]
    # Exact example lookup is incorrect once price and product count change.
    (workspace / "extractor.py").write_text('''import argparse,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument("--manifest");p.add_argument("--output");a=p.parse_args()
Path(a.output).write_text(json.dumps({"entries":[{"native_id":"101","variant_id":"201","source_refs":[{"input_id":"spd"}],"prices":[{"kind":"ask","amount":"14.99","original":"14.99","currency":None}]}],"issues":[]}))
''')
    result = harness.evaluate(workspace)
    assert not result["passed"]
    assert "Metamorphic test failed" in " ".join(result["errors"])


def test_offer_comparison_is_multiset_and_does_not_infer_currency():
    extraction = {"entries": [
        {"native_id": "t3_post", "variant_id": None, "prices": [{"amount": "20.00", "kind": "ask"}]},
        {"native_id": "t3_post", "variant_id": None, "prices": [{"amount": "20", "kind": "ask"}]},
    ]}
    oracle = {"offers": [{"native_id": "t3_post", "amount": "20"}]}
    r = harness.compare(extraction, oracle, "hls")
    assert not r["passed"] and r["actual_priced_offers"] == 2
    assert len(r["extra"]) == 1


def test_session_id_from_native_json_events(tmp_path):
    f = tmp_path / "events.jsonl"
    f.write_text('garbage\n{"type":"step_start","sessionID":"ses_123","part":{}}\n')
    assert harness._session_id(f) == "ses_123"


def test_paid_route_rejected_before_invoking_opencode(tmp_path):
    with pytest.raises(ValueError, match="Refusing"):
        harness.run(tmp_path, "openrouter/paid-model", "opencode")


def test_agent_run_repairs_in_same_session(tmp_path, monkeypatch):
    (tmp_path / "captures.json").write_text("{}")
    monkeypatch.setattr(harness.shutil, "which", lambda cmd: "/usr/bin/" + cmd)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    seen = []

    def fake_agent(workspace, model, prompt, logs, session, exe, timeout):
        seen.append((prompt, session))
        return 0, "ses_abc"

    checks = iter([{"passed": False, "errors": ["Missing variant 123"]},
                   {"passed": True, "errors": []}])
    monkeypatch.setattr(harness, "_agent_turn", fake_agent)
    monkeypatch.setattr(harness, "evaluate", lambda *a, **k: next(checks))
    monkeypatch.setattr(harness.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=1, stderr="not installed"))
    result = harness.run(tmp_path, "openrouter/example:free", "opencode", rounds=3)
    assert result["status"] == "passed"
    assert seen[1][1] == "ses_abc"
    assert "Missing variant 123" in seen[1][0]
    assert len(seen) == 2