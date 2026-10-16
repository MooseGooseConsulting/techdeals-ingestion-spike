"""Deterministic output accounting and reference checks, independent of semantics."""

from .contracts import Contract, EvidenceRef, Fact, ParseBatchResult, PreparedUnit
from .evidence import EvidenceIndex


class ValidationIssue(Contract):
    code: str
    path: str
    message: str


def validate_result(
    result: ParseBatchResult, units: list[PreparedUnit], index: EvidenceIndex
) -> list[ValidationIssue]:
    issues = []

    def issue(code, path, message):
        issues.append(ValidationIssue(code=code, path=path, message=message))

    expected = {u.unit_id: u for u in units}
    if len(expected) != len(units):
        raise ValueError("duplicate expected unit_id")
    seen = set()
    for number, unit in enumerate(result.units):
        path = f"units/{number}"
        if unit.unit_id in seen:
            issue("duplicate_unit", path, unit.unit_id)
        seen.add(unit.unit_id)
        if unit.unit_id not in expected:
            issue("unknown_unit", path, unit.unit_id)
            continue
        allowed = {r.capture_id for r in expected[unit.unit_id].input_refs}
        if {r.capture_id for r in unit.input_refs} != allowed:
            issue(
                "input_accounting",
                path,
                "input_refs must account for every capture in this unit",
            )

        def check_refs(value, here, allowed=allowed):
            if isinstance(value, dict):
                if set(value) == {"capture_id", "locator"}:
                    ref = EvidenceRef.model_validate(value)
                    if ref.capture_id not in allowed:
                        issue("capture_scope", here, ref.capture_id)
                    else:
                        try:
                            index.resolve(ref)
                        except KeyError:
                            issue("unresolved_evidence", here, str(value))
                else:
                    for key, child in value.items():
                        check_refs(child, here + "/" + key)
            elif isinstance(value, list):
                for i, child in enumerate(value):
                    check_refs(child, here + "/" + str(i))

        check_refs(unit.model_dump(mode="json"), path)
        if unit.status in ("no_offer", "failed") and unit.offers:
            issue(
                "invalid_disposition",
                path,
                "no_offer/failed cannot contain offers; use partial",
            )
        ids = [o.local_id for o in unit.offers] + [
            x.local_id for o in unit.offers for x in [*o.items, *o.prices]
        ]
        if len(ids) != len(set(ids)):
            issue(
                "duplicate_local_id",
                path,
                "offer/item/price ids must be unique within unit",
            )
        valid = set(ids)

        def require_fact_evidence(fact: Fact, here: str):
            if fact.value is not None and not fact.evidence:
                issue("missing_fact_evidence", here, "non-null facts require evidence")

        for offer in unit.offers:
            offer_path = path + "/offers/" + offer.local_id
            scope_ids = {offer.local_id} | {item.local_id for item in offer.items}
            if offer.title and not offer.evidence:
                issue(
                    "missing_offer_evidence",
                    offer_path,
                    "offer title requires evidence",
                )
            for key, fact in offer.fields.items():
                require_fact_evidence(fact, offer_path + "/fields/" + key)
            if offer.identity_claim is not None:
                require_fact_evidence(offer.identity_claim, offer_path + "/identity_claim")
            for item in offer.items:
                if (item.title or item.quantity is not None) and not item.evidence:
                    issue(
                        "missing_item_evidence",
                        offer_path + "/items/" + item.local_id,
                        "item title/quantity requires evidence",
                    )
                for key, fact in item.attributes.items():
                    require_fact_evidence(fact, offer_path + "/items/" + item.local_id + "/" + key)
            for price in offer.prices:
                if price.amount is not None:
                    if not price.evidence:
                        issue("missing_price_evidence", offer_path, price.local_id)
                    if not price.scope_refs:
                        issue("missing_price_scope", offer_path, price.local_id)
                for condition in price.conditions:
                    require_fact_evidence(condition, offer_path + "/prices/" + price.local_id)
                for ref in price.scope_refs:
                    if ref not in scope_ids:
                        issue("invalid_price_scope", path, ref)
            for relationship in offer.relations:
                for ref in (relationship.subject_ref, relationship.object_ref):
                    if ref not in valid:
                        issue("invalid_relationship", path, ref)
    for missing in expected.keys() - seen:
        issue("missing_unit", "units", missing)
    return issues


def require_valid_result(
    result: ParseBatchResult, units: list[PreparedUnit], index: EvidenceIndex
) -> ParseBatchResult:
    issues = validate_result(result, units, index)
    if issues:
        raise ValueError("; ".join(f"{i.code} at {i.path}: {i.message}" for i in issues))
    return result
