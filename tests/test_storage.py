from datetime import UTC, datetime
from decimal import Decimal

from techdeals_ingestion.models import (
    Entry,
    Extraction,
    Price,
    Source,
    SourceDate,
    SourceIssue,
    SourceRef,
)
from techdeals_ingestion.storage import _meaning, _safe_error, _typed_source_refs, semantic_key


def source(input_id, acquisition_key, *, content=b"native", retrieved_at=None, namespace="shop"):
    return Source(
        input_id=input_id,
        acquisition_key=acquisition_key,
        content=content,
        media_type="application/json",
        namespace=namespace,
        retrieved_at=retrieved_at,
        url="https://shop.example/items/42",
        metadata={"feed": "daily"},
    )


def test_semantic_key_ignores_receipt_id_and_retrieval_time():
    first = source("capture-a", "fetch-a")
    later = source("capture-b", "fetch-b", retrieved_at=datetime(2026, 10, 16, tzinfo=UTC))
    assert semantic_key([first], "parser-v1") == semantic_key([later], "parser-v1")
    assert semantic_key([first], "parser-v1") != semantic_key([first], "parser-v2")
    assert semantic_key([first], "parser-v1") != semantic_key(
        [source("capture-c", "fetch-c", content=b"changed")], "parser-v1"
    )
    assert semantic_key([first], "parser-v1") != semantic_key(
        [source("capture-d", "fetch-d", namespace="other")], "parser-v1"
    )


def test_cache_rebinding_only_changes_typed_source_refs():
    extraction = Extraction(
        issues=[SourceIssue(input_id="old", message="source note")],
        entries=[
            Entry(
                source_refs=[SourceRef(input_id="old", locator="$.items[0]")],
                title="Server",
                attributes={"vendor_payload": {"input_id": "old"}},
                prices=[
                    Price(
                        amount=Decimal("12.50"),
                        currency=None,
                        original="$12.50",
                        kind="ask",
                        source_refs=[SourceRef(input_id="old", locator="json:/price")],
                    )
                ],
                source_dates=[
                    SourceDate(
                        original_field="ends",
                        value="2026-11-01",
                        meaning="auction end",
                        source_refs=[SourceRef(input_id="old")],
                    )
                ],
            )
        ],
    )
    rebound = _typed_source_refs(extraction, {"old": "new"})
    assert rebound.entries[0].source_refs[0].input_id == "new"
    assert rebound.entries[0].prices[0].source_refs[0].input_id == "new"
    assert rebound.entries[0].source_dates[0].source_refs[0].input_id == "new"
    assert rebound.issues[0].input_id == "new"
    assert rebound.entries[0].attributes == {"vendor_payload": {"input_id": "old"}}
    assert rebound.entries[0].prices[0].amount == Decimal("12.50")
    assert rebound.entries[0].source_dates[0].value == "2026-11-01"


def test_entry_meaning_excludes_storage_refs_but_preserves_native_attributes_and_dates():
    first = Entry(
        source_refs=[SourceRef(input_id="old")],
        native_id="listing-7",
        attributes={"native": {"input_id": "part-of-source-data"}},
        source_dates=[SourceDate(original_field="published", value="yesterday")],
        prices=[Price(amount=Decimal("7.125"), currency="USD", original="$7.125", kind="ask")],
    )
    later = first.model_copy(
        update={
            "source_refs": [SourceRef(input_id="new")],
            "prices": [
                Price(
                    amount=Decimal("7.125"),
                    currency="USD",
                    original="$7.125",
                    kind="ask",
                    source_refs=[SourceRef(input_id="new", locator="json:/price")],
                )
            ],
            "source_dates": [
                SourceDate(
                    original_field="published",
                    value="yesterday",
                    source_refs=[SourceRef(input_id="new")],
                )
            ],
        }
    )
    assert _meaning(first) == _meaning(later)
    assert _meaning(first)["attributes"] == {"native": {"input_id": "part-of-source-data"}}
    assert _meaning(first) != _meaning(
        later.model_copy(
            update={"source_dates": [SourceDate(original_field="published", value="today")]}
        )
    )


def test_failure_message_redacts_common_inline_credentials():
    message = _safe_error(
        "connection postgresql://operator:verysecret@db.example/app failed; api_key=abc123"
    )
    assert "verysecret" not in message and "abc123" not in message
    assert "[redacted]" in message
