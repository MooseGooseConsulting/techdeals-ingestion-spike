"""Storage integration checks for an explicitly configured isolated Neon branch."""

import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from techdeals_ingestion.models import Entry, Extraction, Price, Source, SourceDate, SourceRef
from techdeals_ingestion.storage import Store


@pytest.fixture
def store():
    dsn = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("isolated Neon DATABASE_URL not supplied")
    value = Store(dsn)
    value.migrate()
    return value


def make_source(input_id, acquisition_key, content, *, retrieved_at=None, namespace="storage-test"):
    return Source(
        input_id=input_id,
        acquisition_key=acquisition_key,
        content=content,
        media_type="text/plain",
        namespace=namespace,
        retrieved_at=retrieved_at,
        url="https://example.invalid/listing/" + namespace,
        metadata={"test_token": namespace},
    )


def extraction(input_id, amount, *, issues=None):
    return Extraction(
        entries=[
            Entry(
                source_refs=[SourceRef(input_id=input_id, locator="$.listing")],
                title="Server listing",
                native_id="server-42",
                variant_id="16-core",
                seller="Example seller",
                location="Austin",
                condition="Used",
                availability="One available; pickup only",
                attributes={"memory_gb": 128, "native": {"source_field": "memory"}},
                source_dates=[
                    SourceDate(
                        original_field="published_at",
                        value="2026-10-15",
                        meaning="publication date",
                    )
                ],
                prices=[
                    Price(
                        amount=Decimal(amount),
                        currency="USD",
                        original=f"${amount}",
                        kind="ask",
                        basis="whole server",
                        conditions="pickup only",
                    )
                ],
                issues=[],
            )
        ],
        issues=issues or [],
    )


def test_unknown_observation_time_receipt_idempotence_and_explicit_cache_reuse(store):
    token = uuid4().hex
    first = make_source(
        token + "-a", token + "-fetch-a", b"Server listing at $1200", namespace=token
    )
    store.retain([first])
    store.retain([first])
    saved = store.save([first], extraction(first.input_id, "1200.00"), "parser-v1")
    assert saved["saved_entries"] == 1
    assert store.save([first], extraction(first.input_id, "1200.00"), "parser-v1") == saved
    same_capture_cache = store.cached([first], "parser-v1")
    assert same_capture_cache is not None
    assert same_capture_cache[1] == saved["attempt_id"]
    assert (
        store.save([first], same_capture_cache[0], "parser-v1", reuse_of=same_capture_cache[1])
        == saved
    )
    entries = store.query("entries", attempt_id=saved["attempt_id"])
    prices = store.query("prices", attempt_id=saved["attempt_id"])
    assert len(entries) == 1
    assert entries[0]["native_id"] == "server-42"
    assert entries[0]["observed_at"] is None
    assert entries[0]["attributes"]["memory_gb"] == 128
    assert len(prices) == 1 and prices[0]["amount"] == Decimal("1200.00")
    assert prices[0]["conditions"] == "pickup only"
    assert store.query("current_entries", native_id="server-42", namespace=token) == []

    later = make_source(
        token + "-b",
        token + "-fetch-b",
        b"Server listing at $1200",
        retrieved_at=datetime(2026, 10, 16, tzinfo=UTC),
        namespace=token,
    )
    store.retain([later])
    cached = store.cached([later], "parser-v1")
    assert cached is not None
    reused_result, origin_attempt_id = cached
    assert origin_attempt_id == saved["attempt_id"]
    assert reused_result.entries[0].source_refs[0].input_id == later.input_id
    reused = store.save([later], reused_result, "parser-v1", reuse_of=origin_attempt_id)
    assert reused["saved_entries"] == 1
    current = store.query("current_entries", native_id="server-42", namespace=token)
    assert len(current) == 1 and current[0]["observed_at"] == later.retrieved_at
    assert store.query("current_prices", attempt_id=reused["attempt_id"])[0]["amount"] == Decimal(
        "1200.00"
    )


def test_retention_survives_failure_and_a_b_a_history_is_cached(store):
    token = uuid4().hex
    day1 = datetime(2026, 10, 10, tzinfo=UTC)
    day2 = datetime(2026, 10, 11, tzinfo=UTC)
    day2_late = datetime(2026, 10, 11, 12, tzinfo=UTC)
    day3 = datetime(2026, 10, 12, tzinfo=UTC)
    a1 = make_source(token + "-a1", token + "-fetch-a1", b"A", retrieved_at=day1, namespace=token)
    b = make_source(token + "-b", token + "-fetch-b", b"B", retrieved_at=day2, namespace=token)
    a2 = make_source(token + "-a2", token + "-fetch-a2", b"A", retrieved_at=day3, namespace=token)
    b_late = make_source(
        token + "-b-late", token + "-fetch-b-late", b"B", retrieved_at=day2_late, namespace=token
    )
    for source in (a1, b, a2, b_late):
        store.retain([source])
    failed = store.save([a1], None, "parser-v1", error="ProviderError: token=secret-value rejected")
    failure = store.query("failures", attempt_id=failed["attempt_id"])
    assert len(failure) == 1 and "secret-value" not in failure[0]["error"]

    first = store.save([a1], extraction(a1.input_id, "10"), "parser-v1")
    middle = store.save([b], extraction(b.input_id, "20"), "parser-v1")
    cached_a = store.cached([a2], "parser-v1")
    assert cached_a is not None and cached_a[1] == first["attempt_id"]
    final = store.save([a2], cached_a[0], "parser-v1", reuse_of=cached_a[1])
    assert all(row["saved_entries"] == 1 for row in (first, middle, final))
    cached_late_b = store.cached([b_late], "parser-v1")
    assert cached_late_b is not None and cached_late_b[1] == middle["attempt_id"]
    late = store.save([b_late], cached_late_b[0], "parser-v1", reuse_of=cached_late_b[1])
    assert late["saved_entries"] == 1
    assert len(store.query("history", native_id="server-42", namespace=token)) == 4
    assert len(store.query("entries", attempt_id=first["attempt_id"])) == 1
    assert (
        store.query("entries", attempt_id=final["attempt_id"])[0]["reuse_of"] == first["attempt_id"]
    )
    current = store.query("current_entries", native_id="server-42", namespace=token)
    current_prices = store.query("current_prices", native_id="server-42", namespace=token)
    assert len(current) == len(current_prices) == 1
    assert current[0]["observed_at"] == day3
    assert current_prices[0]["amount"] == Decimal(10)


def test_equal_observation_conflicts_are_explicit_and_partial_result_does_not_refresh(store):
    token = uuid4().hex
    observed_at = datetime(2026, 10, 16, 12, tzinfo=UTC)
    one = make_source(
        token + "-one", token + "-fetch-one", b"One", retrieved_at=observed_at, namespace=token
    )
    two = make_source(
        token + "-two", token + "-fetch-two", b"Two", retrieved_at=observed_at, namespace=token
    )
    later = make_source(
        token + "-partial",
        token + "-fetch-partial",
        b"Partial",
        retrieved_at=datetime(2026, 10, 17, 12, tzinfo=UTC),
        namespace=token,
    )
    for source in (one, two, later):
        store.retain([source])
    store.save([one], extraction(one.input_id, "10"), "parser-v1")
    store.save([two], extraction(two.input_id, "11"), "parser-v1")
    current = store.query("current_entries", native_id="server-42", namespace=token)
    assert len(current) == 2 and all(row["equal_time_conflict"] for row in current)
    store.save(
        [later],
        extraction(
            later.input_id, "9", issues=[{"input_id": later.input_id, "message": "truncated"}]
        ),
        "parser-v1",
    )
    current_after_partial = store.query("current_entries", native_id="server-42", namespace=token)
    assert len(current_after_partial) == 2
    assert all(row["observed_at"] == observed_at for row in current_after_partial)
