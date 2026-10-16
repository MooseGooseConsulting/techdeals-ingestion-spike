"""Deterministic output accounting and reference checks, independent of semantics."""
from .contracts import Contract, EvidenceRef, ParseBatchResult, PreparedUnit
from .evidence import EvidenceIndex

class ValidationIssue(Contract):
    code: str
    path: str
    message: str

def validate_result(result: ParseBatchResult, units: list[PreparedUnit], index: EvidenceIndex) -> list[ValidationIssue]:
    issues = []
    def issue(code, path, message):
        issues.append(ValidationIssue(code=code, path=path, message=message))
    expected = {u.unit_id: u for u in units}
    if len(expected) != len(units):
        raise ValueError('duplicate expected unit_id')
    seen = set()
    for number, unit in enumerate(result.units):
        path = f'units/{number}'
        if unit.unit_id in seen:
            issue('duplicate_unit', path, unit.unit_id)
        seen.add(unit.unit_id)
        if unit.unit_id not in expected:
            issue('unknown_unit', path, unit.unit_id)
            continue
        allowed = {r.capture_id for r in expected[unit.unit_id].input_refs}
        if {r.capture_id for r in unit.input_refs} != allowed:
            issue('input_accounting', path, 'input_refs must account for every capture in this unit')
        def check_refs(value, here):
            if isinstance(value, dict):
                if set(value) == {'capture_id', 'locator'}:
                    ref = EvidenceRef.model_validate(value)
                    if ref.capture_id not in allowed:
                        issue('capture_scope', here, ref.capture_id)
                    else:
                        try:
                            index.resolve(ref)
                        except KeyError:
                            issue('unresolved_evidence', here, str(value))
                else:
                    for key, child in value.items():
                        check_refs(child, here + '/' + key)
            elif isinstance(value, list):
                for i, child in enumerate(value):
                    check_refs(child, here + '/' + str(i))
        check_refs(unit.model_dump(mode='json'), path)
        if unit.status in ('no_offer', 'failed') and unit.offers:
            issue('invalid_disposition', path, 'no_offer/failed cannot contain offers; use partial')
        ids = [o.local_id for o in unit.offers] + [x.local_id for o in unit.offers for x in [*o.items, *o.prices]]
        if len(ids) != len(set(ids)):
            issue('duplicate_local_id', path, 'offer/item/price ids must be unique within unit')
        valid = set(ids)
        scope_ids = {o.local_id for o in unit.offers} | {i.local_id for o in unit.offers for i in o.items}
        for offer in unit.offers:
            for price in offer.prices:
                for ref in price.scope_refs:
                    if ref not in scope_ids:
                        issue('invalid_price_scope', path, ref)
            for relationship in offer.relations:
                for ref in (relationship.subject_ref, relationship.object_ref):
                    if ref not in valid:
                        issue('invalid_relationship', path, ref)
    for missing in expected.keys() - seen:
        issue('missing_unit', 'units', missing)
    return issues

def require_valid_result(result: ParseBatchResult, units: list[PreparedUnit], index: EvidenceIndex) -> ParseBatchResult:
    issues = validate_result(result, units, index)
    if issues:
        raise ValueError('; '.join(f'{i.code} at {i.path}: {i.message}' for i in issues))
    return result
