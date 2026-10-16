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


def test_trial_sql_excludes_older_observation_with_same_unit(tmp_path):
    import os
    from decimal import Decimal
    from uuid import uuid4

    from techdeals_ingestion.contracts import (
        EvidenceRef,
        Offer,
        ParseBatchResult,
        Price,
        UnitResult,
    )
    from techdeals_ingestion.evidence import EvidenceIndex
    from techdeals_ingestion.storage import Store

    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL not supplied; requires an explicit isolated database")
    store = Store(dsn)
    store.migrate()
    experiment = uuid4().hex
    for number in (1, 2):
        cid = experiment + str(number)
        source = tmp_path / cid
        source.write_text(f"Item {number}")
        capture = CaptureInput(
            capture_id=cid,
            acquisition_key=cid,
            path=source,
            media_type="text/plain",
            linked_group="same-unit",
        )
        package = WorkPackage(package_id=cid, captures=[capture])
        ref = EvidenceRef(capture_id=cid, locator="$")
        result = ParseBatchResult(
            batch_id=cid,
            units=[
                UnitResult(
                    unit_id="same-unit",
                    input_refs=[ref],
                    status="complete",
                    offers=[
                        Offer(
                            local_id="offer",
                            title=f"Item {number}",
                            evidence=[ref],
                            prices=[
                                Price(
                                    local_id="price",
                                    amount=Decimal(number),
                                    kind="ask",
                                    scope_refs=["offer"],
                                    evidence=[ref],
                                )
                            ],
                        )
                    ],
                )
            ],
        )
        store.publish(package, result, EvidenceIndex([capture]), experiment, "test", cid)
    assert len(store.query("listing_sightings", experiment_id=experiment, arm="test")) == 2
    rows, prices = cli.trial_rows(store, experiment + "2", experiment, "test")
    assert [row["title"] for row in rows] == ["Item 2"]
    assert [price["amount"] for price in prices] == [Decimal(2)]


async def test_published_partial_unit_is_reported_as_incomplete(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager, nullcontext

    from techdeals_ingestion import extraction
    from techdeals_ingestion.contracts import EvidenceRef, Offer, ParseBatchResult, UnitResult

    source = tmp_path / "source"
    source.write_text("Item, price unavailable")
    package = WorkPackage(
        package_id="p",
        captures=[
            CaptureInput(
                capture_id="item",
                acquisition_key="item",
                path=source,
                media_type="text/plain",
            )
        ],
    )
    ref = EvidenceRef(capture_id="item", locator="$")
    result = ParseBatchResult(
        batch_id="p",
        units=[
            UnitResult(
                unit_id="item",
                input_refs=[ref],
                status="partial",
                issues=["Price unavailable"],
                offers=[Offer(local_id="offer", title="Item", evidence=[ref])],
            )
        ],
    )

    async def parse(*args):
        return result

    @asynccontextmanager
    async def session(*args):
        yield SimpleNamespace(stats=dict)

    connection = SimpleNamespace(execute=lambda *a: SimpleNamespace(fetchall=list))
    store = SimpleNamespace(
        retain_package=lambda p: None,
        publish=lambda *a, **kw: {"published_units": 1, "errors": []},
        connect=lambda: nullcontext(connection),
        query=lambda *a, **kw: [],
    )
    monkeypatch.setattr(cli, "_store", lambda: store)
    monkeypatch.setattr(cli, "load_work_packages", lambda p: [package])
    monkeypatch.setattr(cli, "LLMRun", session)
    monkeypatch.setattr(extraction, "parse_direct", parse)
    args = SimpleNamespace(
        manifest="unused",
        config="config/parser.toml",
        package="p",
        arm="staged",
        experiment="partial",
        output=str(tmp_path / "trial"),
        fresh=True,
        direct=True,
        attempt="partial-attempt",
    )
    assert await cli.run_package(args) == 1
    summary = json.loads((tmp_path / "trial/summary.json").read_text())
    assert summary["publication"] == {"published_units": 1, "errors": []}
    assert summary["status"] == "completed_with_failures"
