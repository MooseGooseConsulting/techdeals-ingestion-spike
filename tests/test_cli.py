import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from techdeals_ingestion import cli
from techdeals_ingestion import parser as parser_module
from techdeals_ingestion.models import Entry, Extraction, Price, SourceRef


def args_for(path, output, **extra):
    defaults = {
        "input": [str(path)],
        "package": None,
        "manifest": "unused",
        "namespace": "shop",
        "url": "https://example.test/item",
        "retrieved_at": None,
        "acquisition_key": None,
        "metadata": {},
        "config": "unused",
        "output": str(output),
        "fresh": True,
    }
    defaults.update(extra)
    return SimpleNamespace(**defaults)


async def test_ingest_retains_then_parses_once_and_writes_attempt_snapshots(tmp_path, monkeypatch):
    path = tmp_path / "item.json"
    path.write_bytes(b'{"title":"Disk","price":"120"}')
    output = tmp_path / "run"
    events = []

    class Settings:
        def model_dump(self, **kwargs):
            return {"model": "test-model"}

    class Store:
        def retain(self, sources):
            events.append(("retain", sources[0].content))

        def cached(self, sources, key):
            events.append(("cached", key))

        def save(self, sources, result, key, **kwargs):
            events.append(("save", result is not None, kwargs))
            return {
                "attempt_id": "attempt-1",
                "saved_entries": len(result.entries) if result else 0,
            }

        def query(self, view, **filters):
            events.append(("query", view, filters))
            return [{"view": view}]

    result = None

    async def parse(sources, settings, trace_dir):
        nonlocal result
        events.append(("parse", sources[0].content, trace_dir.exists()))
        assert events[0] == ("retain", path.read_bytes())
        result = Extraction(
            entries=[
                Entry(
                    source_refs=[SourceRef(input_id=sources[0].input_id)],
                    title="Disk",
                    prices=[Price(amount="120", currency="USD", original="$120", kind="ask")],
                )
            ]
        )
        return result

    monkeypatch.setattr(parser_module, "fingerprint", lambda settings: "parser-key")
    monkeypatch.setattr(parser_module, "parse", parse)
    monkeypatch.setattr(cli, "_store", lambda: Store())
    monkeypatch.setattr(cli.ParserSettings, "load", lambda path: Settings())

    assert await cli._ingest(args_for(path, output)) == 0
    assert [event[0] for event in events] == ["retain", "parse", "save", "query", "query"]
    assert events[1] == ("parse", path.read_bytes(), True)
    assert json.loads((output / "result.json").read_text())["entries"][0]["title"] == "Disk"
    report = json.loads((output / "report.json").read_text())
    assert report["attempt_id"] == "attempt-1"
    assert report["status"] == "completed"
    assert json.loads((output / "database-entries.json").read_text()) == [{"view": "entries"}]
    assert (output / "trace").is_dir()


async def test_zero_entries_without_a_source_issue_is_incomplete(tmp_path, monkeypatch):
    path = tmp_path / "empty.txt"
    path.write_text("nothing recognizable")
    output = tmp_path / "run"

    class Settings:
        def model_dump(self, **kwargs):
            return {}

    class Store:
        def retain(self, sources):
            pass

        def cached(self, sources, key):
            return None

        def save(self, sources, result, key, **kwargs):
            return {"attempt_id": "attempt-2"}

        def query(self, view, **filters):
            return []

    monkeypatch.setattr(parser_module, "fingerprint", lambda settings: "key")

    async def parse(*args, **kwargs):
        return Extraction()

    monkeypatch.setattr(parser_module, "parse", parse)
    monkeypatch.setattr(cli, "_store", lambda: Store())
    monkeypatch.setattr(cli.ParserSettings, "load", lambda path: Settings())
    assert await cli._ingest(args_for(path, output)) == 1
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "completed_with_issues"


async def test_provider_exception_text_is_never_saved_or_reported(tmp_path, monkeypatch):
    path = tmp_path / "item.txt"
    path.write_text("native item")
    output = tmp_path / "run"
    persisted_errors = []

    class Settings:
        def model_dump(self, **kwargs):
            return {}

    class ProviderFailure(Exception):
        status_code = 502

    class Store:
        def retain(self, sources):
            pass

        def cached(self, sources, key):
            return None

        def save(self, sources, result, key, **kwargs):
            persisted_errors.append(kwargs.get("error"))
            return {"attempt_id": "failed-attempt", "saved_entries": 0}

        def query(self, view, **filters):
            raise AssertionError("failed extraction must not query entries")

    monkeypatch.setattr(parser_module, "fingerprint", lambda settings: "key")

    async def parse(*args):
        raise ProviderFailure("provider body contains TOP_SECRET_KEY")

    monkeypatch.setattr(parser_module, "parse", parse)
    monkeypatch.setattr(cli, "_store", lambda: Store())
    monkeypatch.setattr(cli.ParserSettings, "load", lambda path: Settings())

    assert await cli._ingest(args_for(path, output)) == 1
    assert "TOP_SECRET_KEY" not in persisted_errors[0]
    report_text = (output / "report.json").read_text()
    assert "TOP_SECRET_KEY" not in report_text
    report = json.loads(report_text)
    assert report["error"] == {"type": "ProviderFailure", "status_code": 502}


async def test_validation_failure_keeps_a_sanitized_message(tmp_path, monkeypatch):
    path = tmp_path / "item.txt"
    path.write_text("native item")
    output = tmp_path / "run"

    class Settings:
        def model_dump(self, **kwargs):
            return {}

    class Store:
        def retain(self, sources):
            pass

        def cached(self, sources, key):
            return None

        def save(self, sources, result, key, **kwargs):
            return {"attempt_id": "failed-attempt", "saved_entries": 0}

        def query(self, view, **filters):
            raise AssertionError("failed extraction must not query entries")

    monkeypatch.setattr(parser_module, "fingerprint", lambda settings: "key")

    async def parse(*args):
        raise ValueError("batch exceeds max_input_bytes; token=secret-value")

    monkeypatch.setattr(parser_module, "parse", parse)
    monkeypatch.setattr(cli, "_store", lambda: Store())
    monkeypatch.setattr(cli.ParserSettings, "load", lambda path: Settings())

    assert await cli._ingest(args_for(path, output)) == 1
    report = json.loads((output / "report.json").read_text())
    assert report["error"]["type"] == "ValueError"
    assert "max_input_bytes" in report["error"]["message"]
    assert "secret-value" not in report["error"]["message"]


async def test_snapshot_failure_preserves_the_saved_extraction(tmp_path, monkeypatch):
    path = tmp_path / "item.json"
    path.write_bytes(b'{"title":"Disk"}')
    output = tmp_path / "run"
    saves = []

    class Settings:
        def model_dump(self, **kwargs):
            return {}

    class Store:
        def retain(self, sources):
            pass

        def cached(self, sources, key):
            return None

        def save(self, sources, result, key, **kwargs):
            saves.append(result is not None)
            return {"attempt_id": "attempt-1", "saved_entries": 1}

        def query(self, view, **filters):
            raise RuntimeError("snapshot dropped")

    async def parse(sources, settings, trace_dir):
        return Extraction(
            entries=[Entry(source_refs=[SourceRef(input_id=sources[0].input_id)], title="Disk")]
        )

    monkeypatch.setattr(parser_module, "fingerprint", lambda settings: "parser-key")
    monkeypatch.setattr(parser_module, "parse", parse)
    monkeypatch.setattr(cli, "_store", lambda: Store())
    monkeypatch.setattr(cli.ParserSettings, "load", lambda path: Settings())

    assert await cli._ingest(args_for(path, output)) == 1
    assert saves == [True]
    assert json.loads((output / "result.json").read_text())["entries"][0]["title"] == "Disk"
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "completed"
    assert report["attempt_id"] == "attempt-1"
    assert report["snapshot_error"] == {"type": "RuntimeError"}


async def test_invalid_config_does_not_leave_an_output_directory(tmp_path):
    path = tmp_path / "item.txt"
    path.write_text("native")
    output = tmp_path / "run"
    with pytest.raises(FileNotFoundError):
        await cli._ingest(args_for(path, output, config=str(tmp_path / "missing.toml")))
    assert not output.exists()


def test_store_rejects_database_outside_exercise_target(tmp_path, monkeypatch):
    config = tmp_path / "neon.toml"
    config.write_text(
        'project_id = "p"\n'
        'branch_id = "b"\n'
        'database = "ingestion_spike"\n'
        'role = "owner"\n'
        'host = "ep-example.example.neon.tech"\n'
    )
    monkeypatch.setattr(cli, "_target_config_path", lambda: config)
    direct = "postgresql://ep-example.example.neon.tech/ingestion_spike"
    pooled = "postgresql://ep-example-pooler.example.neon.tech/ingestion_spike"
    cli._require_exercise_target(direct)
    cli._require_exercise_target(pooled)
    with pytest.raises(ValueError, match="outside the configured exercise database"):
        cli._require_exercise_target("postgresql://other.example/ingestion_spike")


def test_target_config_ignores_working_directory_copy(tmp_path, monkeypatch):
    work = tmp_path / "config"
    work.mkdir()
    (work / "neon.toml").write_text('host = "cwd.example"\ndatabase = "other"\n')
    monkeypatch.chdir(tmp_path)
    assert cli._target_config_path() == Path(__file__).resolve().parents[1] / "config" / "neon.toml"


def test_store_rejects_query_parameters_that_override_the_connection_target(tmp_path, monkeypatch):
    config = tmp_path / "neon.toml"
    config.write_text('host = "ep-example.example.neon.tech"\ndatabase = "ingestion_spike"\n')
    monkeypatch.setattr(cli, "_target_config_path", lambda: config)
    with pytest.raises(ValueError, match="outside the configured exercise database"):
        cli._require_exercise_target(
            "postgresql://ep-example.example.neon.tech/ingestion_spike?hostaddr=203.0.113.10"
        )
