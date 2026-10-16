"""Independent, deliberately bounded oracle checks; never an input to a parser.

A passed structural/amount check is not a semantic pass. Requirements whose meaning
cannot be established by exact typed assertions remain visible for human review.
"""

from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

from .contracts import ParseBatchResult, WorkPackage


def _check(requirement, expected, actual, source, locator, status=None):
    return {
        "requirement": requirement,
        "status": status or ("pass" if actual == expected else "fail"),
        "expected": expected,
        "actual": actual,
        "source": source,
        "native_locator": locator,
    }


def _matches(offer, expected):
    """Exact asserted native identity, never a title word similarity match."""
    if expected.get("variant_id") is not None:
        return offer.fields.variant_id.value == str(expected["variant_id"])
    if expected.get("native_id") is not None:
        return offer.fields.native_id.value == str(expected["native_id"])
    key = expected.get("obs_id")
    fact = offer.additional_facts.get("obs_id")
    return key is not None and fact is not None and str(key) == str(fact.value)


def evaluate(result: ParseBatchResult, package: WorkPackage, oracle_path) -> dict:
    oracle = json.loads(Path(oracle_path).read_text())
    source = str(oracle_path)
    cases = []
    capture_ids = {c.capture_id for c in package.captures}
    for case_id, spec in oracle["cases"].items():
        ids = set(spec.get("capture_ids", [case_id]))
        if not ids.intersection(capture_ids):
            continue
        units = [u for u in result.units if any(r.capture_id in ids for r in u.input_refs)]
        offers = [o for u in units for o in u.offers]
        checks = []

        def add(name, expected, actual, locator="$", status=None, _checks=checks):
            _checks.append(_check(name, expected, actual, source, locator, status))

        add(
            "input captures retained",
            sorted(ids & capture_ids),
            sorted({r.capture_id for u in units for r in u.input_refs if r.capture_id in ids}),
        )
        expected_offers = spec.get("offers", spec.get("rows", []))
        # Social trade/wanted dispositions can legitimately be represented as offers
        # with an explicit kind, so their exact representation needs human review.
        if case_id not in {"hls", "hws"}:
            add(
                "offer count (no repeated root/HTML representation)",
                spec.get("expected_offer_count", spec.get("expected_rows", len(expected_offers))),
                len(offers),
            )
        for ordinal, expected in enumerate(expected_offers):
            loc = expected.get("locator", f"cases.{case_id}.offers[{ordinal}]")
            candidates = [o for o in offers if _matches(o, expected)]
            add("native option identity retained uniquely", 1, len(candidates), loc)
            if len(candidates) != 1:
                add("price association", expected, None, loc, "not_attempted")
                continue
            offer = candidates[0]
            amount = expected.get("amount", expected.get("claims", {}).get("price_usd"))
            if amount is not None:
                prices = [p for p in offer.prices if p.amount == Decimal(amount)]
                add("native option amount retained", True, bool(prices), loc)
                if "currency" in expected:
                    add(
                        "currency associated with native option",
                        expected["currency"],
                        prices[0].currency if len(prices) == 1 else [p.currency for p in prices],
                        loc,
                    )
                if "kind" in expected:
                    add(
                        "price kind",
                        expected["kind"],
                        prices[0].kind if len(prices) == 1 else None,
                        loc,
                    )
                valid_refs = {offer.local_id, *(i.local_id for i in offer.items)}
                add(
                    "price has valid explicit scope references",
                    True,
                    bool(prices)
                    and all(p.scope_refs and set(p.scope_refs) <= valid_refs for p in prices),
                    loc,
                )
            for name in (
                "components",
                "memory",
                "cpu_quantity",
                "scope",
                "price_scope",
                "store_counts",
                "available",
                "availability",
                "shipping_available",
            ):
                if name in expected:
                    add(
                        name,
                        expected[name],
                        offer.model_dump(mode="json"),
                        loc,
                        "manual_review_required",
                    )
            if "claims" in expected:
                add(
                    "export claims retained as claims, not verified listing truth",
                    expected,
                    offer.model_dump(mode="json"),
                    loc,
                    "manual_review_required",
                )
        if "currency" in spec.get("unknowns", []):
            add(
                "unknown currency not invented",
                [],
                [p.currency for o in offers for p in o.prices if p.currency is not None],
            )
        for name in (
            "forbidden",
            "unknowns",
            "conflicts",
            "source_dates",
            "dispositions",
            "requirements",
            "derived_claims",
        ):
            values = spec.get(name, [])
            if isinstance(values, dict):
                values = [values]
            for index, expected in enumerate(values):
                add(
                    name,
                    expected,
                    [u.model_dump(mode="json") for u in units],
                    f"cases.{case_id}.{name}[{index}]",
                    "manual_review_required",
                )
        if "entry_occurrences" in spec:
            add(
                "every authored entry occurrence disposed including duplicate IDs",
                spec["entry_occurrences"],
                [u.model_dump(mode="json") for u in units],
                status="manual_review_required",
            )
        cases.append({"case_id": case_id, "checks": checks})
    return {
        "package_id": package.package_id,
        "cases": cases,
        "gaps": oracle.get("gaps", []),
        "limits": [
            (
                "No aggregate semantic verdict. Exact identity, count, amount, currency and "
                "reference checks do not establish all attributes or source meaning."
            ),
            (
                "Manual requirements require native evidence review, including dates, "
                "bundle contents, conflict preservation, exported claims and store inventory."
            ),
        ],
    }


def evaluate_persisted(
    result: ParseBatchResult, listing_sightings, price_rows=None, current_listings=None
) -> dict:
    """Compare Store.query rows filtered to this attempt/experiment and arm.

    Include SQL prices to test NUMERIC storage, and current_listings to test date
    gating. A listing_sightings JSON payload alone cannot establish either.
    """
    rows = list(listing_sightings)
    checks = []
    expected = {(u.unit_id, o.local_id): o for u in result.units for o in u.offers}
    keys = [(r["unit_id"], r["local_id"]) for r in rows]
    checks.append(
        _check(
            "persisted offer occurrences",
            Counter(expected.keys()),
            Counter(keys),
            "listing_sightings",
            "$",
        )
    )
    for key, offer in expected.items():
        matched = [r for r in rows if (r["unit_id"], r["local_id"]) == key]
        actual = matched[0]["payload"] if len(matched) == 1 else None
        checks.append(
            _check(
                "complete offer payload round trip",
                offer.model_dump(mode="json"),
                actual,
                "listing_sightings",
                str(key),
            )
        )
    if price_rows is None:
        checks.append(
            _check(
                "SQL numeric price preservation",
                "prices query",
                None,
                "prices",
                "$",
                "not_attempted",
            )
        )
    else:
        expected_prices = Counter(
            (o.local_id, p.local_id, p.amount, p.currency, p.kind)
            for o in expected.values()
            for p in o.prices
        )
        actual_prices = Counter(
            (
                r["offer_id"],
                r["local_id"],
                Decimal(str(r["amount"])) if r["amount"] is not None else None,
                r["currency"],
                r["kind"],
            )
            for r in price_rows
        )
        checks.append(
            _check("SQL numeric price preservation", expected_prices, actual_prices, "prices", "$")
        )
    if current_listings is None:
        checks.append(
            _check(
                "unresolved dates excluded from current view",
                "dated rows only",
                None,
                "current_listings",
                "$",
                "not_attempted",
            )
        )
    else:
        checks.append(
            _check(
                "unresolved dates excluded from current view",
                [],
                [r["local_id"] for r in current_listings if r.get("observed_at") is None],
                "current_listings",
                "$",
            )
        )
    for unit in result.units:
        unit_rows = [r for r in rows if r["unit_id"] == unit.unit_id]
        payloads = [r.get("unit_payload") for r in unit_rows]
        if not payloads or any(p is None for p in payloads):
            status = "not_attempted"
            actual = None
        else:
            actual = payloads[0]
            status = "pass" if all(p == unit.model_dump(mode="json") for p in payloads) else "fail"
        checks.append(
            _check(
                "complete unit dispositions and issues round trip",
                unit.model_dump(mode="json"),
                actual,
                "listing_sightings.unit_payload",
                unit.unit_id,
                status,
            )
        )
    # Counters with tuple keys are useful internally, but reports must be JSON serializable.
    for check in checks:
        for field in ("expected", "actual"):
            if isinstance(check[field], Counter):
                check[field] = sorted([[str(k), v] for k, v in check[field].items()])
    return {"checks": checks, "limits": ["SQL round trip establishes retention, not source truth."]}
