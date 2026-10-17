from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from techdeals_ingestion.models import (
    Entry,
    Extraction,
    Price,
    Source,
    SourceDate,
    SourceIssue,
    SourceRef,
)
from techdeals_ingestion.storage import (
    _identity_key,
    _meaning,
    _rebind_mapping,
    _safe_error,
    _typed_source_refs,
    semantic_key,
)


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


def test_rebind_keeps_identical_linked_groups_separate():
    def item(input_id, group, digest):
        return {"input_id": input_id, "context_digest": digest, "linked_group": group}

    old = [
        item("a1", "left", "c1"),
        item("a2", "left", "c2"),
        item("b1", "right", "c1"),
        item("b2", "right", "c2"),
    ]
    new = [
        item("n1", "one", "c1"),
        item("n2", "one", "c2"),
        item("m1", "two", "c1"),
        item("m2", "two", "c2"),
    ]
    mapping = _rebind_mapping(old, new)
    assert mapping is not None
    left = {mapping["a1"], mapping["a2"]}
    right = {mapping["b1"], mapping["b2"]}
    assert left in ({"n1", "n2"}, {"m1", "m2"})
    assert right in ({"n1", "n2"}, {"m1", "m2"})
    assert left != right


def test_sku_correction_stays_inside_the_stable_listing_identity():
    rows = [{"namespace": "shop"}]
    first = Entry(
        source_refs=[SourceRef(input_id="capture")],
        native_id="post-1",
        variant_id="v1",
        seller="ada",
        sku="OLD-SKU",
    )
    corrected = first.model_copy(update={"sku": "NEW-SKU"})
    assert _identity_key(first, rows) == _identity_key(corrected, rows)
    assert _identity_key(first, rows) != _identity_key(
        first.model_copy(update={"native_id": "post-2"}), rows
    )


def test_current_view_keeps_compatible_facts_and_partial_replacements():
    sql = Path("src/techdeals_ingestion/schema.sql").read_text()
    view = sql.split("CREATE VIEW ingest_current_entries AS", 1)[1]
    view = view.split("CREATE VIEW ingest_current_prices", 1)[0]
    assert "GROUP BY acquisition_keys, identity_key" in view
    assert "count(DISTINCT f.semantic_hash)" not in view
    assert "count(DISTINCT f.fact_key)" in view
    assert "d - 'source_refs'" in view
    assert "AND identity_key IS NOT NULL" in view


def test_failure_message_redacts_common_inline_credentials():
    message = _safe_error(
        "connection postgresql://operator:verysecret@db.example/app failed; api_key=abc123"
    )
    assert "verysecret" not in message and "abc123" not in message
    assert "[redacted]" in message
