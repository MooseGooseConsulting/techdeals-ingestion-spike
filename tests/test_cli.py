"""Integration boundaries: failed preparation still retains source evidence."""

import json
from types import SimpleNamespace

import pytest

from techdeals_ingestion import cli
from techdeals_ingestion.contracts import CaptureInput, WorkPackage


async def test_bad_native_syntax_retained_and_failure_recorded(tmp_path, monkeypatch):
    source = tmp_path / "broken.json"
    source.write_bytes(b'{"unfinished":')
    package = WorkPackage(
        package_id="broken",
        captures=[
            CaptureInput(
                capture_id="raw",
                acquisition_key="acquisition",
                path=source,
                media_type="application/json",
            )
        ],
    )
    calls = []

    class FakeStore:
        def retain_package(self, value):
            calls.append(("retained", value.captures[0].path.read_bytes()))

        def record_failure(self, value, experiment, arm, attempt, reason):
            calls.append(("failed", reason))
            return {"published_units": 0, "errors": [reason]}

    monkeypatch.setattr(cli, "_store", lambda: FakeStore())
    monkeypatch.setattr(cli, "load_work_packages", lambda _: [package])
    monkeypatch.setattr(cli, "LLMRun", lambda *a, **k: pytest.fail("No LLM on invalid preparation"))
    args = SimpleNamespace(
        manifest="unused",
        config="config/parser.toml",
        package="broken",
        arm="staged",
        experiment="test",
        output=str(tmp_path / "trial"),
        fresh=True,
        attempt="failed-preparation",
    )
    assert await cli.run_package(args) == 1
    assert calls[0] == ("retained", source.read_bytes())
    assert calls[1][0] == "failed"
    summary = json.loads((tmp_path / "trial/summary.json").read_text())
    assert summary["exception_type"] == "JSONDecodeError"
    with pytest.raises(ValueError, match="new output directory"):
        await cli.run_package(args)


def test_fingerprint_changes_with_shared_instructions(monkeypatch):
    from techdeals_ingestion.settings import ParserSettings

    settings = ParserSettings.load()
    original = cli.parser_fingerprint(settings, "staged")
    read = cli.Path.read_bytes

    def changed(path):
        return read(path) + (b"different semantics" if path.name == "instructions.py" else b"")

    monkeypatch.setattr(cli.Path, "read_bytes", changed)
    assert cli.parser_fingerprint(settings, "staged") != original
