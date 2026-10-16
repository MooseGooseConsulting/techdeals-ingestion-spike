import pytest
from pydantic import ValidationError

from techdeals_ingestion.contracts import (
    CaptureInput,
    ParseBatchResult,
    WorkPackage,
    prepare_units,
)
from techdeals_ingestion.evidence import EvidenceIndex
from techdeals_ingestion.validation import validate_result


def setup(tmp_path):
    captures = []
    for cid in ["a", "b"]:
        p = tmp_path / cid
        p.write_text("{}")
        captures.append(
            CaptureInput(
                capture_id=cid,
                acquisition_key=cid,
                path=p,
                media_type="application/json",
            )
        )
    return prepare_units(WorkPackage(package_id="p", captures=captures)), EvidenceIndex(captures)


def unit(cid):
    return {
        "unit_id": cid,
        "input_refs": [{"capture_id": cid, "locator": "$"}],
        "status": "complete",
        "offers": [],
    }


def test_out_of_order_and_missing_accounting(tmp_path):
    units, index = setup(tmp_path)
    assert not validate_result(
        ParseBatchResult(batch_id="x", units=[unit("b"), unit("a")]), units, index
    )
    issues = validate_result(
        ParseBatchResult(batch_id="x", units=[unit("a"), unit("a")]), units, index
    )
    assert {i.code for i in issues} == {"duplicate_unit", "missing_unit"}


def test_scope_and_resolution(tmp_path):
    units, index = setup(tmp_path)
    a = unit("a")
    a["offers"] = [
        {
            "local_id": "o",
            "additional_facts": {
                "bad": {
                    "name": "bad",
                    "evidence": [{"capture_id": "b", "locator": "$"}],
                }
            },
            "prices": [{"local_id": "p", "kind": "ask", "scope_refs": ["missing"]}],
            "relations": [{"subject_ref": "o", "predicate": "contains", "object_ref": "missing"}],
        }
    ]
    result = ParseBatchResult(batch_id="x", units=[a, unit("b")])
    assert {i.code for i in validate_result(result, units, index)} == {
        "capture_scope",
        "invalid_price_scope",
        "invalid_relationship",
        "missing_relationship_evidence",
    }
    a["input_refs"][0]["locator"] = "json:/missing"
    assert "unresolved_evidence" in {
        i.code
        for i in validate_result(ParseBatchResult(batch_id="x", units=[a, unit("b")]), units, index)
    }


def test_unknown_metadata_is_rejected():
    with pytest.raises(ValidationError):
        ParseBatchResult(batch_id="x", units=[], retrieved_at="2020-01-01")


def test_unsupported_facts_titles_and_prices_rejected(tmp_path):
    units, index = setup(tmp_path)
    a = unit("a")
    a["offers"] = [
        {
            "local_id": "o",
            "title": "unsupported",
            "additional_facts": {"capacity": {"name": "capacity", "value": 64}},
            "items": [{"local_id": "i", "role": "included", "title": "DIMM"}],
            "prices": [{"local_id": "p", "kind": "ask", "amount": "12"}],
        }
    ]
    issues = validate_result(ParseBatchResult(batch_id="x", units=[a, unit("b")]), units, index)
    assert {i.code for i in issues} == {
        "missing_offer_evidence",
        "missing_fact_evidence",
        "missing_item_evidence",
        "missing_price_evidence",
        "missing_price_scope",
    }
    ref = {"capture_id": "a", "locator": "$"}
    offer = a["offers"][0]
    offer["evidence"] = [ref]
    offer["additional_facts"]["capacity"]["evidence"] = [ref]
    offer["items"][0]["evidence"] = [ref]
    offer["prices"][0].update(evidence=[ref], scope_refs=["i"])
    assert not validate_result(ParseBatchResult(batch_id="x", units=[a, unit("b")]), units, index)


def test_price_cannot_cover_unrelated_offer_in_same_unit(tmp_path):
    units, index = setup(tmp_path)
    a = unit("a")
    a["offers"] = [
        {
            "local_id": "o1",
            "prices": [
                {
                    "local_id": "p",
                    "kind": "ask",
                    "amount": "12",
                    "evidence": [{"capture_id": "a", "locator": "$"}],
                    "scope_refs": ["i2"],
                }
            ],
        },
        {"local_id": "o2", "items": [{"local_id": "i2", "role": "offered"}]},
    ]
    assert {
        i.code
        for i in validate_result(ParseBatchResult(batch_id="x", units=[a, unit("b")]), units, index)
    } == {"invalid_price_scope"}


def test_dates_and_relationships_require_actual_source_references(tmp_path):
    units, index = setup(tmp_path)
    a = unit("a")
    a["offers"] = [
        {
            "local_id": "o",
            "source_dates": [
                {"original_field": "auction_end", "value": "2026-10-20", "meaning": "auction end"}
            ],
            "relations": [{"subject_ref": "o", "predicate": "contains", "object_ref": "i"}],
            "items": [{"local_id": "i", "role": "included"}],
        }
    ]
    result = ParseBatchResult(batch_id="x", units=[a, unit("b")])
    assert {i.code for i in validate_result(result, units, index)} == {
        "missing_date_evidence",
        "missing_relationship_evidence",
    }
    ref = {"capture_id": "a", "locator": "$"}
    a["offers"][0]["source_dates"][0]["evidence"] = [ref]
    a["offers"][0]["relations"][0]["evidence"] = [ref]
    assert not validate_result(ParseBatchResult(batch_id="x", units=[a, unit("b")]), units, index)


def test_offer_columns_are_explicit_typed_and_preserve_unknowns():
    from decimal import Decimal

    from techdeals_ingestion.contracts import Offer, OfferFields

    offer = Offer.model_validate(
        {
            "local_id": "o",
            "fields": {
                "seller": {"name": "seller", "value": "Shop", "original": "Shop LLC"},
                "available_quantity": {"name": "stock", "value": "123456789.123456789"},
            },
            "additional_facts": {"vendor": {"name": "vendor", "value": "Manufacturer"}},
        }
    )
    assert offer.fields.seller.value == "Shop"
    assert offer.fields.available_quantity.value == Decimal("123456789.123456789")
    assert offer.fields.location.value is None
    assert offer.fields.location.status == "unknown"
    payload = offer.model_dump(mode="json")
    assert payload["fields"]["available_quantity"]["value"] == "123456789.123456789"
    assert payload["fields"]["seller"]["original"] == "Shop LLC"
    assert payload["additional_facts"]["vendor"]["value"] == "Manufacturer"
    assert Offer.model_validate(payload) == offer
    assert set(OfferFields.model_json_schema()["properties"]) == {
        "seller",
        "location",
        "condition",
        "availability",
        "description",
        "native_id",
        "variant_id",
        "intent",
        "url",
        "available_quantity",
    }


@pytest.mark.parametrize("value", [123, True, ["Shop"], {"seller": "Shop"}])
def test_text_columns_reject_non_string_values(value):
    from techdeals_ingestion.contracts import Offer

    with pytest.raises(ValidationError):
        Offer.model_validate(
            {"local_id": "o", "fields": {"seller": {"name": "seller", "value": value}}}
        )


def test_named_columns_require_evidence_for_values(tmp_path):
    units, index = setup(tmp_path)
    a = unit("a")
    a["offers"] = [
        {
            "local_id": "o",
            "fields": {
                "seller": {"name": "seller", "value": "Shop"},
                "available_quantity": {"name": "stock", "value": "2.5"},
            },
        }
    ]
    issues = validate_result(ParseBatchResult(batch_id="x", units=[a, unit("b")]), units, index)
    assert {(i.code, i.path) for i in issues} == {
        ("missing_fact_evidence", "units/0/offers/o/fields/seller"),
        ("missing_fact_evidence", "units/0/offers/o/fields/available_quantity"),
    }
