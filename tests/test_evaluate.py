import json
from pathlib import Path

from techdeals_ingestion.contracts import (
    CaptureInput,
    EvidenceRef,
    Fact,
    Offer,
    ParseBatchResult,
    Price,
    UnitResult,
    WorkPackage,
)
from techdeals_ingestion.evaluate import evaluate, evaluate_persisted


def fixture(tmp_path, *, currency=None, scope=("offer",)):
    oracle = tmp_path / "oracle.json"
    oracle.write_text(
        json.dumps(
            {
                "cases": {
                    "test": {
                        "offers": [
                            {"native_id": "native-123", "amount": "12.50", "currency": None}
                        ],
                        "unknowns": ["currency"],
                    }
                },
                "gaps": ["authored fixture"],
            }
        )
    )
    package = WorkPackage(
        package_id="p",
        captures=[
            CaptureInput(
                capture_id="test", acquisition_key="k", path=Path("unused"), media_type="text/plain"
            )
        ],
    )
    offer = Offer(
        local_id="offer",
        fields={"native_id": Fact(name="native_id", value="native-123")},
        prices=[
            Price(
                local_id="price",
                amount="12.50",
                currency=currency,
                kind="ask",
                scope_refs=list(scope),
            )
        ],
    )
    result = ParseBatchResult(
        batch_id="b",
        units=[
            UnitResult(
                unit_id="test",
                status="complete",
                input_refs=[EvidenceRef(capture_id="test", locator="$")],
                offers=[offer],
            )
        ],
    )
    return oracle, package, result


def checks(report):
    return {c["requirement"]: c for c in report["cases"][0]["checks"]}


def test_missing_offer_fails_count_and_identity(tmp_path):
    oracle, package, result = fixture(tmp_path)
    result.units[0].offers.clear()
    observed = checks(evaluate(result, package, oracle))
    assert observed["offer count (no repeated root/HTML representation)"]["status"] == "fail"
    assert observed["native option identity retained uniquely"]["status"] == "fail"


def test_false_currency_and_wrong_scope_fail(tmp_path):
    oracle, package, result = fixture(tmp_path, currency="USD", scope=("unrelated",))
    observed = checks(evaluate(result, package, oracle))
    assert observed["unknown currency not invented"]["status"] == "fail"
    assert observed["price has valid explicit scope references"]["status"] == "fail"


def test_title_match_does_not_establish_identity(tmp_path):
    oracle, package, result = fixture(tmp_path)
    result.units[0].offers[0].fields.clear()
    result.units[0].offers[0].title = "native-123"
    assert (
        checks(evaluate(result, package, oracle))["native option identity retained uniquely"][
            "status"
        ]
        == "fail"
    )


def test_roundtrip_detects_dropped_fact(tmp_path):
    _, _, result = fixture(tmp_path)
    offer = result.units[0].offers[0]
    payload = offer.model_dump(mode="json")
    payload["fields"] = {}
    report = evaluate_persisted(
        result, [{"unit_id": "test", "local_id": "offer", "payload": payload}]
    )
    assert report["checks"][1]["status"] == "fail"
    assert report["checks"][2]["status"] == "not_attempted"
    json.dumps(report)


def test_roundtrip_decimal_and_date_gating(tmp_path):
    _, _, result = fixture(tmp_path)
    offer = result.units[0].offers[0]
    report = evaluate_persisted(
        result,
        [{"unit_id": "test", "local_id": "offer", "payload": offer.model_dump(mode="json")}],
        price_rows=[
            {
                "offer_id": "offer",
                "local_id": "price",
                "amount": "12.51",
                "currency": None,
                "kind": "ask",
            }
        ],
        current_listings=[{"local_id": "offer", "observed_at": None}],
    )
    assert report["checks"][2]["status"] == "fail"
    assert report["checks"][3]["status"] == "fail"
