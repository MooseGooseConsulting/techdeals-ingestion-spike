"""Run only against an explicitly supplied isolated PostgreSQL test database."""

import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from techdeals_ingestion.contracts import (
    CaptureInput,
    EvidenceRef,
    Offer,
    ParseBatchResult,
    Price,
    UnitResult,
    WorkPackage,
)
from techdeals_ingestion.evidence import EvidenceIndex
from techdeals_ingestion.storage import Store


@pytest.fixture
def store():
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL not supplied; requires an explicit isolated database")
    value = Store(dsn)
    value.migrate()
    return value


def test_retention_partial_publication_and_reuse(store, tmp_path):
    token = uuid4().hex
    path = tmp_path / "raw"
    path.write_text("Server $42")

    def make(cid, acquisition, dated=False):
        capture = CaptureInput(
            capture_id=cid,
            acquisition_key=acquisition,
            path=path,
            media_type="text/plain",
            source_namespace="test",
            retrieved_at=datetime.now(UTC) if dated else None,
            metadata={"identity_bindings": {"$": {"confirmed": True, "native_id": token}}},
        )
        return WorkPackage(package_id=cid, captures=[capture])

    package = make(token, token)
    index = EvidenceIndex(package.captures)
    store.retain_package(package)  # survives any later inference failure
    ref = EvidenceRef(capture_id=token, locator="$")
    result = ParseBatchResult(
        batch_id=token,
        units=[
            UnitResult(
                unit_id=token,
                input_refs=[ref],
                status="complete",
                offers=[
                    Offer(
                        local_id="offer",
                        title="Server",
                        evidence=[ref],
                        prices=[
                            Price(
                                local_id="price",
                                amount=Decimal(42),
                                currency=None,
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
    report = store.publish(package, result, index, token, "a", token, parser_fingerprint="v1")
    assert report["published_units"] == 1
    assert len(store.query("undated_evidence", experiment_id=token, arm="a")) == 1
    assert not store.query("current_listings", experiment_id=token, arm="a")
    assert (
        store.publish(package, result, index, token, "a", token, parser_fingerprint="v1") == report
    )
    newer = make(token + "new", token + "new", dated=True)
    reused = store.reuse(newer, EvidenceIndex(newer.captures), token, "a", "v1")
    assert reused is not None
    assert reused.units[0].offers[0].evidence[0].capture_id == token + "new"
    store.publish(
        newer,
        reused,
        EvidenceIndex(newer.captures),
        token,
        "a",
        token + "new",
        parser_fingerprint="v1",
    )
    assert len(store.query("current_listings", experiment_id=token, arm="a")) == 1
    assert store.query("current_prices", experiment_id=token, arm="a")[0]["amount"] == Decimal(42)
    assert len(store.query("listing_sightings", experiment_id=token, arm="a")) == 2
    assert store.reuse(newer, EvidenceIndex(newer.captures), token, "b", "v1") is None


def test_history_revisions_partial_failures_and_foreign_keys(store, tmp_path):
    import psycopg

    token = uuid4().hex
    path = tmp_path / "listing"
    sightings = []
    for ordinal, (day, amount) in enumerate([(3, "10"), (1, "10"), (2, "20"), (4, "10")]):
        cid = f"{token}-{ordinal}"
        path.write_text(f"Native {token} ask {amount}")
        capture = CaptureInput(
            capture_id=cid,
            acquisition_key=cid,
            path=path,
            media_type="text/plain",
            source_namespace="test",
            retrieved_at=datetime(2026, 1, day, tzinfo=UTC),
            metadata={"identity_bindings": {"$": {"confirmed": True, "native_id": token}}},
        )
        package = WorkPackage(package_id=cid, captures=[capture])
        ref = EvidenceRef(capture_id=cid, locator="$")
        result = ParseBatchResult(
            batch_id=cid,
            units=[
                UnitResult(
                    unit_id=cid,
                    input_refs=[ref],
                    status="complete",
                    offers=[
                        Offer(
                            local_id="offer",
                            evidence=[ref],
                            prices=[
                                Price(
                                    local_id="price",
                                    amount=Decimal(amount),
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
        store.publish(
            package, result, EvidenceIndex([capture]), token, "a", cid, parser_fingerprint="v1"
        )
        sightings.append(store.query("listing_sightings", experiment_id=token, arm="a")[-1])
    assert len(store.query("listing_changes", experiment_id=token, arm="a")) == 3
    assert store.query("current_prices", experiment_id=token, arm="a")[0]["amount"] == Decimal(10)
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT result_id,observation_id FROM interpretations WHERE experiment_id=%s", (token,)
        ).fetchall()
        with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
            conn.execute(
                "INSERT INTO accepted VALUES (%s,%s,%s,%s)",
                (rows[0]["observation_id"], token, "invalid", rows[1]["result_id"]),
            )
    # A revision selects a new interpretation of the same retained acquisition.
    result.units[0].offers[0].title = "revised interpretation"
    store.publish(
        package,
        result,
        EvidenceIndex([capture]),
        token,
        "a",
        cid + "revision",
        parser_fingerprint="v2",
    )
    assert len(store.query("listing_sightings", experiment_id=token, arm="a")) == 4
    assert (
        store.query("current_listings", experiment_id=token, arm="a")[0]["title"]
        == "revised interpretation"
    )

    # One invalid unit cannot suppress an independently valid sibling's partial evidence.
    second_path = tmp_path / "second"
    second_path.write_text("Other entry")
    bad = capture.model_copy(
        update={"capture_id": token + "bad", "acquisition_key": token + "bad", "path": second_path}
    )
    combined = WorkPackage(package_id=token + "partial", captures=[capture, bad])
    bad_ref = EvidenceRef(capture_id=bad.capture_id, locator="$")
    malformed = UnitResult(
        unit_id=bad.capture_id,
        input_refs=[bad_ref],
        status="partial",
        offers=[
            Offer(
                local_id="bad",
                prices=[Price(local_id="bad-price", kind="ask", scope_refs=["nonexistent"])],
            )
        ],
    )
    combined_result = ParseBatchResult(
        batch_id=token + "partial", units=[result.units[0], malformed]
    )
    report = store.publish(
        combined,
        combined_result,
        EvidenceIndex(combined.captures),
        token,
        "partial",
        token + "partial",
        parser_fingerprint="v2",
    )
    assert report["published_units"] == 1
    assert len(report["errors"]) == 1
    with store.connect() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM captures WHERE capture_id=%s", (bad.capture_id,)
            ).fetchone()["n"]
            == 1
        )


def test_strong_constraints_savepoints_and_preparation_failures(store, tmp_path, monkeypatch):
    import psycopg

    token = uuid4().hex
    path = tmp_path / "raw"
    path.write_text("entry")
    captures = [
        CaptureInput(
            capture_id=token + str(i),
            acquisition_key=token + str(i),
            path=path,
            media_type="text/plain",
        )
        for i in range(2)
    ]
    package = WorkPackage(package_id=token, captures=captures)
    units = [
        UnitResult(
            unit_id=c.capture_id,
            input_refs=[EvidenceRef(capture_id=c.capture_id, locator="$")],
            status="complete",
            offers=[
                Offer(
                    local_id="offer",
                    title=str(i),
                    evidence=[EvidenceRef(capture_id=c.capture_id, locator="$")],
                )
            ],
        )
        for i, c in enumerate(captures)
    ]
    original = store._listing

    def fail_second(conn, offer, capture_map):
        if offer.title == "1":
            conn.execute("SELECT 1/0")  # Actual database failure, after the first unit succeeds.
        return original(conn, offer, capture_map)

    monkeypatch.setattr(store, "_listing", fail_second)
    report = store.publish(
        package,
        ParseBatchResult(batch_id=token, units=units),
        EvidenceIndex(captures),
        token,
        "a",
        token,
    )
    assert report["published_units"] == 1 and len(report["errors"]) == 1
    assert len(store.query("ingestion_failures", experiment_id=token, arm="a")) == 1
    with store.connect() as conn:
        row = conn.execute(
            "SELECT * FROM interpretations WHERE experiment_id=%s", (token,)
        ).fetchone()
        for experiment, arm in [(token, "other"), (token + "other", "a")]:
            with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
                conn.execute(
                    "INSERT INTO accepted VALUES (%s,%s,%s,%s)",
                    (row["observation_id"], experiment, arm, row["result_id"]),
                )
    alias = captures[0].model_copy(update={"capture_id": token + "alias"})
    with pytest.raises(psycopg.errors.UniqueViolation):
        store.retain_package(WorkPackage(package_id="alias", captures=[alias]))
    broken = captures[0].model_copy(
        update={
            "capture_id": token + "broken",
            "acquisition_key": token + "broken",
            "media_type": "application/json",
        }
    )
    failure_package = WorkPackage(package_id=token + "broken", captures=[broken])
    first = store.record_failure(failure_package, token, "prep", token + "prep", "invalid JSON")
    assert (
        store.record_failure(failure_package, token, "prep", token + "prep", "invalid JSON")
        == first
    )
    assert len(store.query("ingestion_failures", experiment_id=token, arm="prep")) == 1
    with pytest.raises(ValueError, match="collision"):
        store.record_failure(failure_package, token, "prep", token + "prep", "different reason")


def test_equal_time_conflicts_and_selected_reuse(store, tmp_path):
    token = uuid4().hex
    date = datetime(2026, 1, 1, tzinfo=UTC)
    path = tmp_path / "raw"
    path.write_text("identical meaning")

    def publish(suffix, amount="10", status="complete"):
        cid = token + suffix
        c = CaptureInput(
            capture_id=cid,
            acquisition_key=cid,
            path=path,
            media_type="text/plain",
            source_namespace="test",
            retrieved_at=date,
            metadata={"identity_bindings": {"$": {"confirmed": True, "native_id": token}}},
        )
        p = WorkPackage(package_id=cid, captures=[c])
        r = EvidenceRef(capture_id=cid, locator="$")
        unit = UnitResult(
            unit_id=cid,
            input_refs=[r],
            status=status,
            offers=[
                Offer(
                    local_id="offer" + suffix,
                    evidence=[r],
                    prices=[
                        Price(
                            local_id="price" + suffix,
                            amount=Decimal(amount),
                            kind="ask",
                            scope_refs=["offer" + suffix],
                            evidence=[r],
                        )
                    ],
                )
            ],
        )
        store.publish(
            p,
            ParseBatchResult(batch_id=cid, units=[unit]),
            EvidenceIndex([c]),
            token,
            "a",
            cid,
            parser_fingerprint="v1",
        )
        return p, unit

    p, unit = publish("1")
    publish("2")
    assert len(store.query("listing_sightings", experiment_id=token)) == 2
    assert len(store.query("current_listings", experiment_id=token)) == 1
    assert len(store.query("current_prices", experiment_id=token)) == 1
    assert all(
        not row["equal_time_conflict"]
        for row in store.query("current_listings", experiment_id=token)
    )
    publish("3", amount="20")
    assert len(store.query("current_listings", experiment_id=token)) == 2
    assert len(store.query("current_prices", experiment_id=token)) == 2
    assert len(store.query("listing_sightings", experiment_id=token)) == 3
    assert all(
        row["equal_time_conflict"] for row in store.query("current_listings", experiment_id=token)
    )
    assert not store.query("listing_changes", experiment_id=token)
    # Replacing a selection by partial makes that interpretation ineligible for reuse.
    for row in store.query("listing_sightings", experiment_id=token):
        with store.connect() as conn:
            conn.execute("DELETE FROM accepted WHERE result_id=%s", (row["result_id"],))
    unit.status = "partial"
    store.publish(
        p,
        ParseBatchResult(batch_id=token + "partial", units=[unit]),
        EvidenceIndex(p.captures),
        token,
        "a",
        token + "partial",
        parser_fingerprint="v1",
    )
    assert store.reuse(p, EvidenceIndex(p.captures), token, "a", "v1") is None


def test_source_date_only_changes_and_equal_time_conflicts(store, tmp_path):
    from techdeals_ingestion.contracts import SourceDate

    token = uuid4().hex

    def publish_date(number, observed_day, end_day):
        cid = token + str(number)
        path = tmp_path / cid
        path.write_text(f"Auction end 2026-10-{end_day}")
        capture = CaptureInput(
            capture_id=cid,
            acquisition_key=cid,
            path=path,
            media_type="text/plain",
            source_namespace="test",
            retrieved_at=datetime(2026, 10, observed_day, tzinfo=UTC),
            metadata={"identity_bindings": {"$": {"confirmed": True, "native_id": token}}},
        )
        ref = EvidenceRef(capture_id=cid, locator="$")
        result = ParseBatchResult(
            batch_id=cid,
            units=[
                UnitResult(
                    unit_id=cid,
                    input_refs=[ref],
                    status="complete",
                    offers=[
                        Offer(
                            local_id="offer",
                            title="Auction",
                            evidence=[ref],
                            source_dates=[
                                SourceDate(
                                    original_field="auction_end",
                                    value=f"2026-10-{end_day}",
                                    meaning="auction end",
                                    evidence=[ref],
                                )
                            ],
                            prices=[
                                Price(
                                    local_id="price",
                                    amount=Decimal(42),
                                    kind="current_bid",
                                    scope_refs=["offer"],
                                    evidence=[ref],
                                )
                            ],
                        )
                    ],
                )
            ],
        )
        store.publish(
            WorkPackage(package_id=cid, captures=[capture]),
            result,
            EvidenceIndex([capture]),
            token,
            "dates",
            cid,
            parser_fingerprint="date-test",
        )

    publish_date(1, 1, 20)
    publish_date(2, 2, 21)
    assert len(store.query("listing_changes", experiment_id=token, arm="dates")) == 2
    publish_date(3, 2, 22)
    current = store.query("current_listings", experiment_id=token, arm="dates")
    assert len(current) == 2 and all(row["equal_time_conflict"] for row in current)
    assert len(store.query("listing_changes", experiment_id=token, arm="dates")) == 1


def test_partial_rerun_and_newer_partial_preserve_complete_state(store, tmp_path):
    token = uuid4().hex
    path = tmp_path / "raw"
    path.write_text("Server $100")
    capture = CaptureInput(
        capture_id=token,
        acquisition_key=token,
        path=path,
        media_type="text/plain",
        source_namespace="test",
        retrieved_at=datetime(2026, 1, 2, tzinfo=UTC),
        metadata={"identity_bindings": {"$": {"confirmed": True, "native_id": token}}},
    )

    def publish(capture, suffix, status):
        ref = EvidenceRef(capture_id=capture.capture_id, locator="$")
        offer = Offer(local_id="offer", title="Server", evidence=[ref])
        if status == "complete":
            offer.prices = [
                Price(
                    local_id="price",
                    amount=Decimal(100),
                    kind="ask",
                    scope_refs=["offer"],
                    evidence=[ref],
                )
            ]
        package = WorkPackage(package_id=token + suffix, captures=[capture])
        return store.publish(
            package,
            ParseBatchResult(
                batch_id=token + suffix,
                units=[
                    UnitResult(
                        unit_id=capture.capture_id,
                        input_refs=[ref],
                        status=status,
                        offers=[offer],
                    )
                ],
            ),
            EvidenceIndex([capture]),
            token,
            "a",
            token + suffix,
            parser_fingerprint=suffix,
        )

    publish(capture, "complete", "complete")
    original = store.query("current_listings", experiment_id=token)[0]
    publish(capture, "rerun", "partial")
    assert store.query("current_listings", experiment_id=token)[0] == original
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT payload->>'status' AS status FROM interpretations WHERE experiment_id=%s",
            (token,),
        ).fetchall()
    assert sorted(row["status"] for row in rows) == ["complete", "partial"]
    newer = capture.model_copy(
        update={
            "capture_id": token + "new",
            "acquisition_key": token + "new",
            "retrieved_at": datetime(2026, 1, 3, tzinfo=UTC),
        }
    )
    publish(newer, "newpartial", "partial")
    sightings = store.query("listing_sightings", experiment_id=token)
    assert len(sightings) == 2
    assert any(row["unit_payload"]["status"] == "partial" for row in sightings)
    assert store.query("current_listings", experiment_id=token)[0] == original
    price = store.query("current_prices", experiment_id=token)[0]
    assert price["amount"] == Decimal(100)
    assert price["observed_at"] == datetime(2026, 1, 2, tzinfo=UTC)
    assert len(store.query("listing_changes", experiment_id=token)) == 1
    store.migrate()  # Existing views and their dependents must update repeatably.
    assert store.query("current_listings", experiment_id=token)[0] == original


def test_no_offer_outcome_is_queryable_without_a_listing(store, tmp_path):
    token = uuid4().hex
    path = tmp_path / "notice"
    path.write_text("No products for sale")
    capture = CaptureInput(
        capture_id=token,
        acquisition_key=token,
        path=path,
        media_type="text/plain",
        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    ref = EvidenceRef(capture_id=token, locator="$")
    report = store.publish(
        WorkPackage(package_id=token, captures=[capture]),
        ParseBatchResult(
            batch_id=token,
            units=[
                UnitResult(
                    unit_id=token,
                    input_refs=[ref],
                    status="no_offer",
                    issues=["No offer in source"],
                )
            ],
        ),
        EvidenceIndex([capture]),
        token,
        "a",
        token,
    )
    assert report["published_units"] == 1
    assert not store.query("listing_sightings", experiment_id=token)
    rows = store.query("ingestion_outcomes", experiment_id=token, arm="a")
    assert len(rows) == 1
    assert rows[0]["status"] == "no_offer"
    assert rows[0]["unit_payload"]["issues"] == ["No offer in source"]
    assert rows[0]["observed_at"] == capture.retrieved_at
    assert rows[0]["input_capture_ids"] == [token]
    assert not store.query("ingestion_failures", experiment_id=token)


def test_package_reuse_provenance_is_atomic_and_consumed(store, tmp_path):
    token = uuid4().hex
    paths = []
    for name in ("first", "second"):
        path = tmp_path / name
        path.write_text(name + " offer")
        paths.append(path)

    def package(suffix, count):
        return WorkPackage(
            package_id=token + suffix,
            captures=[
                CaptureInput(
                    capture_id=token + suffix + str(i),
                    acquisition_key=token + suffix + str(i),
                    path=paths[i],
                    media_type="text/plain",
                )
                for i in range(count)
            ],
        )

    def fresh(package, suffix):
        return ParseBatchResult(
            batch_id=token + suffix,
            units=[
                UnitResult(
                    unit_id=c.capture_id,
                    status="complete",
                    input_refs=[EvidenceRef(capture_id=c.capture_id, locator="$")],
                    offers=[
                        Offer(
                            local_id="offer",
                            title=c.path.read_text(),
                            evidence=[EvidenceRef(capture_id=c.capture_id, locator="$")],
                        )
                    ],
                )
                for c in package.captures
            ],
        )

    def publish(package, result, suffix):
        store.publish(
            package,
            result,
            EvidenceIndex(package.captures),
            token,
            "a",
            token + suffix,
            parser_fingerprint="v1",
        )
        with store.connect() as conn:
            return conn.execute(
                "SELECT result_id,reused_from FROM interpretations "
                "WHERE attempt_key=%s ORDER BY unit_id",
                (token + suffix,),
            ).fetchall()

    seed = package("seed", 1)
    publish(seed, fresh(seed, "seed"), "seed")
    mixed = package("mixed", 2)
    assert store.reuse(mixed, EvidenceIndex(mixed.captures), token, "a", "v1") is None
    rows = publish(mixed, fresh(mixed, "fresh"), "fresh")
    assert len(rows) == 2 and all(row["reused_from"] is None for row in rows)

    complete = package("complete", 2)
    reused = store.reuse(complete, EvidenceIndex(complete.captures), token, "a", "v1")
    assert reused is not None
    assert reused.batch_id == complete.package_id
    reused.batch_id = complete.package_id  # CLI normalizes this before publication.
    reused_rows = publish(complete, reused, "reused")
    assert len(reused_rows) == 2 and all(row["reused_from"] for row in reused_rows)
    with store.connect() as conn:
        links = conn.execute(
            "SELECT child.semantic_key=source.semantic_key AS compatible "
            "FROM interpretations child JOIN interpretations source "
            "ON source.result_id=child.reused_from WHERE child.attempt_key=%s",
            (token + "reused",),
        ).fetchall()
    assert len(links) == 2 and all(row["compatible"] for row in links)
    # A fresh batch after a successful reuse lookup must not inherit that lookup.
    assert store.reuse(complete, EvidenceIndex(complete.captures), token, "a", "v1") is not None
    rows = publish(complete, fresh(complete, "replacement"), "replacement")
    assert len(rows) == 2 and all(row["reused_from"] is None for row in rows)
    # The earlier successful batch is also one-shot, after another publish consumes state.
    rows = publish(complete, reused, "again")
    assert len(rows) == 2 and all(row["reused_from"] is None for row in rows)
