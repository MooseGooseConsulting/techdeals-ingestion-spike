"""Fixed, bounded preparation, optional layout discovery, and batched extraction.

Preparation contains no source field mappings. The source index remains authoritative;
compact HTML records retain direct text, attributes, scripts, and hidden values.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag
from pydantic import Field

from .contracts import (
    Contract,
    EvidenceRef,
    ParseBatchResult,
    PreparedUnit,
    UnitResult,
    WorkPackage,
    prepare_units,
)
from .evidence import EvidenceIndex
from .validation import validate_result

CHUNK_BYTES = 64 * 1024

from .instructions import SEMANTIC_INSTRUCTIONS

SEMANTICS = SEMANTIC_INSTRUCTIONS


class Discovery(Contract):
    refs: list[EvidenceRef] = Field(default_factory=list)
    groups: list[list[EvidenceRef]] = Field(
        default_factory=list,
        description="Distinct logical purchases/entries; group all representations and variants of the same product together",
    )
    context_refs: list[EvidenceRef] = Field(default_factory=list)
    disposition: str = Field(description="relevant or no_offer_material")
    issues: list[str] = Field(default_factory=list)


@dataclass
class View:
    unit: PreparedUnit
    records: list[dict]
    issues: list[str]
    parent_id: str | None = None
    coverage: dict | None = None


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _size(value) -> int:
    return len(_dump(value).encode())


def _html_records(ref: EvidenceRef, index: EvidenceIndex) -> list[dict]:
    records = []
    # Traverse indexed DOM locations, retaining each node's own material once.
    pending = [ref]
    while pending:
        current = pending.pop()
        offset = 0
        while True:
            page = index.list_children(current, offset=offset)
            children = [EvidenceRef.model_validate(r) for r in page["refs"]]
            pending.extend(reversed(children))
            if page["next_offset"] is None:
                break
            offset = page["next_offset"]
        if current.locator == "$":
            continue
        soup = BeautifulSoup(index.resolve(current), "html.parser")
        node = next((n for n in soup.contents if isinstance(n, Tag)), None)
        if node is None:
            continue
        text = " ".join(str(x).strip() for x in node.contents if not isinstance(x, Tag)).strip()
        if text or node.attrs:
            records.append(
                {"ref": current.model_dump(), "tag": node.name, "attrs": node.attrs, "text": text}
            )
    return records


def _prepare(unit: PreparedUnit, index: EvidenceIndex) -> View:
    records = []
    for ref in unit.input_refs:
        capture = index.captures[ref.capture_id]
        records.append(
            {
                "ref": ref.model_dump(),
                "collection_context": capture.model_dump(mode="json", exclude={"path"}),
            }
        )
        if capture.media_type.split(";")[0] in ("text/html", "application/xhtml+xml"):
            records.extend(_html_records(ref, index))
        elif "json" in capture.media_type:
            # Generic top-level fields/entries expose fine references for discovery;
            # all nested JSON pointers are indexed and available for group selection.
            value = json.loads(index.resolve(ref))
            entries = (
                value.items()
                if isinstance(value, dict)
                else enumerate(value)
                if isinstance(value, list)
                else [(None, value)]
            )
            for key, child in entries:
                locator = (
                    "json:"
                    if key is None
                    else "json:/" + str(key).replace("~", "~0").replace("/", "~1")
                )
                records.append(
                    {
                        "ref": EvidenceRef(capture_id=ref.capture_id, locator=locator).model_dump(),
                        "text": _dump(child),
                    }
                )
        else:
            records.append({"ref": ref.model_dump(), "text": index.resolve(ref)})
    return View(unit, records, [])


def _wire_records(records):
    """Compact row encoding avoids repeating long field names/capture ids per DOM node."""
    captures = {}
    other = []
    for record in records:
        if "tag" in record:
            ref = record["ref"]
            captures.setdefault(ref["capture_id"], []).append(
                [ref["locator"], record["tag"], record["attrs"], record["text"]]
            )
        else:
            other.append(record)
    return {
        "html_columns": ["locator", "tag", "attributes", "direct_text"],
        "html_by_capture": captures,
        "records": other,
    }


def _chunks(records: list[dict], budget: int = CHUNK_BYTES) -> list[list[dict]]:
    """Complete UTF-8-safe coverage, including a single huge text/script record."""
    pieces = []
    for record in records:
        if _size(_wire_records([record])) <= budget:
            pieces.append(record)
            continue
        text = _dump(record)
        # Store slices as literal material, preserving the original indexed reference.
        while text:
            end = min(len(text), budget // 6)
            pieces.append({"ref": record["ref"], "fragment": text[:end]})
            text = text[end:]
    result, batch = [], []
    for piece in pieces:
        if batch and _size(_wire_records(batch + [piece])) > budget:
            result.append(batch)
            batch = []
        batch.append(piece)
    if batch:
        result.append(batch)
    return result


async def _run(session, output_type, instructions, prompt, *, correction=False):
    kwargs = {"retry_budget": 0} if correction else {}
    agent = session.build_agent(output_type=output_type, instructions=instructions, **kwargs)
    result = await session.run(agent, prompt)
    return result.output


def _descendants(ref: EvidenceRef, index: EvidenceIndex) -> set[tuple[str, str]]:
    found = set()
    pending = [ref]
    while pending:
        current = pending.pop()
        key = (current.capture_id, current.locator)
        if key in found:
            continue
        found.add(key)
        offset = 0
        while True:
            page = index.list_children(current, offset=offset)
            pending.extend(EvidenceRef.model_validate(r) for r in page["refs"])
            if page["next_offset"] is None:
                break
            offset = page["next_offset"]
    return found


def _select_records(refs, view, index):
    selected = set()
    for ref in refs:
        selected.update(_descendants(ref, index))
    records = [
        r
        for r in view.records
        if "collection_context" in r or (r["ref"]["capture_id"], r["ref"]["locator"]) in selected
    ]
    represented = {
        (r["ref"]["capture_id"], r["ref"]["locator"])
        for r in records
        if "collection_context" not in r
    }
    for ref in refs:
        descendants = _descendants(ref, index)
        if ref.locator == "$":
            records = [
                r
                for r in records
                if "collection_context" in r or r["ref"]["capture_id"] != ref.capture_id
            ]
            records.append({"ref": ref.model_dump(), "text": index.resolve(ref)})
        elif not descendants.intersection(represented):
            records.append({"ref": ref.model_dump(), "text": index.resolve(ref)})
    return records


def _overview(view: View, index: EvidenceIndex) -> dict:
    """Syntactic layout only: bounded previews are discovery hints, never extraction evidence."""
    captures = []
    for root in view.unit.input_refs:
        snapshot = index.snapshot([root.capture_id])[0]
        refs = [
            EvidenceRef(capture_id=root.capture_id, locator=r["locator"]) for r in snapshot["refs"]
        ]
        media = index.captures[root.capture_id].media_type
        rows = []
        inventory = [r.locator for r in refs]
        if "html" in media:
            # Canonical preorder aliases form an exact, compact complete inventory.
            numbers = [int(r.locator[6:]) for r in refs if r.locator.startswith("html:@")]
            inventory = {
                "root": "$",
                "html_preorder_range_inclusive": [min(numbers), max(numbers)] if numbers else [],
                "count": len(numbers),
            }
            candidates = set()
            for ref in refs:
                children = []
                offset = 0
                while True:
                    page = index.list_children(ref, offset=offset)
                    children.extend(EvidenceRef.model_validate(r) for r in page["refs"])
                    if page["next_offset"] is None:
                        break
                    offset = page["next_offset"]
                if ref.locator == "$":
                    candidates.update(r.locator for r in children)
                    continue
                soup = BeautifulSoup(index.resolve(ref), "html.parser")
                node = next((n for n in soup.contents if isinstance(n, Tag)), None)
                if node is None:
                    continue
                if node.name in ("script", "input", "meta") or "hidden" in node.attrs:
                    candidates.add(ref.locator)
                # Repeated nontrivial sibling subtrees are potential native entries,
                # without interpreting a CSS class, field name, or source identity.
                if len(children) >= 2:
                    tags = {}
                    for child in children:
                        raw = index.resolve(child)
                        parsed = BeautifulSoup(raw, "html.parser").find()
                        if parsed and len(raw.encode()) >= 512 and len(parsed.find_all()) >= 3:
                            tags.setdefault(parsed.name, []).append(child)
                    for siblings in tags.values():
                        if len(siblings) >= 2:
                            candidates.update(r.locator for r in siblings)
            for ref in refs:
                if ref.locator not in candidates:
                    continue
                soup = BeautifulSoup(index.resolve(ref), "html.parser")
                node = soup.find()
                rows.append(
                    [
                        ref.locator,
                        node.name if node else "text",
                        len(index.resolve(ref).encode()),
                        _dump(node.attrs)[:80] if node else "",
                        (
                            " ".join(str(x) for x in node.contents)
                            if node and node.name == "script"
                            else soup.get_text(" ", strip=True)
                        )[:120],
                    ]
                )
            columns = ["locator", "tag", "source_bytes", "attribute_preview", "text_preview"]
        elif "json" in media:
            inventory = {"all_locators_in_summary_rows": True, "count": len(refs) - 1}
            for ref in refs:
                if not ref.locator.startswith("json:"):
                    continue
                value = json.loads(index.resolve(ref))
                kind = type(value).__name__
                rows.append(
                    [
                        ref.locator,
                        kind,
                        len(value) if isinstance(value, (dict, list, str)) else None,
                        list(value) if isinstance(value, dict) else None,
                        str(value)[:60] if not isinstance(value, (dict, list)) else None,
                    ]
                )
            columns = ["locator", "type", "length", "keys", "scalar_preview"]
        else:
            for ref in refs:
                text = index.resolve(ref)
                rows.append([ref.locator, len(text.encode()), text[:180]])
            columns = ["locator", "source_bytes", "text_preview"]
        captures.append(
            {
                "capture_id": root.capture_id,
                "inventory": inventory,
                "summary_columns": columns,
                "summaries": rows,
            }
        )
    return {
        "captures": captures,
        "preview_warning": "Summaries are bounded syntactic previews only. Full indexed source will be gathered for selected refs.",
    }


def _overview_pages(overview, budget):
    if _size(overview) <= budget:
        return [overview]
    pages = []
    for capture in overview["captures"]:
        base = {**capture, "summaries": []}
        rows = []
        for row in capture["summaries"]:
            if rows and _size({**base, "summaries": rows + [row]}) > budget:
                pages.append({**base, "summaries": rows})
                rows = []
            rows.append(row)
        pages.append({**base, "summaries": rows})
    return pages


async def _discover(view: View, index: EvidenceIndex, session) -> list[View]:
    groups = []
    context = []
    budget = (
        getattr(session, "settings", None).max_prepared_bytes
        if getattr(session, "settings", None)
        else 96 * 1024
    )
    pages = _overview_pages(_overview(view, index), budget - 4096)
    for number, page in enumerate(pages):
        prompt = _dump(
            {
                "unit_id": view.unit.unit_id,
                "overview_page": number,
                "total_pages": len(pages),
                "overview": page,
            }
        )
        try:
            result = await _run(
                session,
                Discovery,
                SEMANTICS
                + "\nLayout discovery from a syntactic overview only. Previews are incomplete; do not extract facts now. Return indexed ref groups, ONE logical product/purchase/native entry per group, preserving variants and grouping duplicate HTML/JSON-LD representations. Select enclosing card/row/object refs so full descendants will be gathered. Select all ordinary fields necessary for complete common facts, descriptions, source dates and prices. Return shared context_refs separately. JSON refs are RFC6901 pointers. HTML inventory gives every valid html:@N alias; listed subtree summaries highlight repeated structure. The complete source remains indexed. Never return code. Mark no_offer_material only if no offer material is present. refs is a fallback for one group.",
                prompt,
            )
            allowed = {r.capture_id for r in view.unit.input_refs}
            for ref in [
                *result.refs,
                *result.context_refs,
                *(r for group in result.groups for r in group),
            ]:
                if ref.capture_id not in allowed:
                    raise ValueError("discovery selected a different capture")
                index.resolve(ref)
            discovered = result.groups or ([result.refs] if result.refs else [])
            if result.disposition != "no_offer_material" and not discovered:
                raise ValueError("relevant discovery overview has no selected groups")
            groups.extend(discovered)
            context.extend(result.context_refs)
            view.issues.extend(result.issues)
        except Exception as exc:  # noqa: BLE001 - isolate provider/schema failures per unit
            view.issues.append(
                f"layout overview {number + 1}/{len(pages)} failed: {type(exc).__name__}"
            )
    # Merge groups sharing an exact indexed reference: source identity, not title guessing.
    merged = []
    for group in groups:
        keys = {(r.capture_id, r.locator) for r in group}
        overlaps = [g for g in merged if keys.intersection((r.capture_id, r.locator) for r in g)]
        for old in overlaps:
            group.extend(old)
            merged.remove(old)
        merged.append(list({(r.capture_id, r.locator): r for r in group}.values()))
    if not merged:
        return [View(view.unit, _select_records(context, view, index), view.issues)]
    views = []
    for group in merged:
        token = hashlib.sha256(
            _dump(sorted((r.capture_id, r.locator) for r in group)).encode()
        ).hexdigest()[:12]
        subunit = PreparedUnit(
            unit_id=view.unit.unit_id + "#" + token, input_refs=view.unit.input_refs
        )
        views.append(
            View(
                subunit,
                _select_records([*context, *group], view, index),
                list(view.issues),
                view.unit.unit_id,
                {
                    "selected_refs": [r.model_dump() for r in [*context, *group]],
                    "indexed_regions": sum(
                        len(x["refs"])
                        for x in index.snapshot([r.capture_id for r in view.unit.input_refs])
                    ),
                    "deferred_regions": "Complete indexed inventory minus descendant closure of selected_refs",
                    "coverage_meaning": "Selected refs include full descendant closure; remaining indexed regions are deferred, not semantically verified.",
                },
            )
        )
    return views


def _payload(views: list[View]) -> dict:
    return {
        "units": [
            {
                "unit_id": v.unit.unit_id,
                "input_refs": [r.model_dump() for r in v.unit.input_refs],
                "material": _wire_records(v.records),
                "layout_coverage": v.coverage,
            }
            for v in views
        ]
    }


async def parse_batch(package: WorkPackage, index: EvidenceIndex, session) -> ParseBatchResult:
    from .settings import ParserSettings

    settings = getattr(session, "settings", None) or ParserSettings.load()
    max_bytes = settings.max_prepared_bytes
    units = prepare_units(package)
    if (
        sum(len(index.originals[c.capture_id]) for c in package.captures)
        > settings.max_package_bytes
    ):
        raise ValueError("package exceeds 512 KiB allowed source limit")
    views = []
    failures = {}
    for unit in units:
        try:
            view = _prepare(unit, index)
            # Collections and large bodies need model-selected native entry groups.
            collection = any(len(index.list_children(ref)["refs"]) > 1 for ref in unit.input_refs)
            if not collection:
                for ref in unit.input_refs:
                    if "json" in index.captures[ref.capture_id].media_type:
                        value = json.loads(index.resolve(ref))
                        collection = isinstance(value, list) or (
                            isinstance(value, dict)
                            and any(isinstance(v, list) and len(v) > 1 for v in value.values())
                        )
            prepared = (
                await _discover(view, index, session)
                if collection or _size(_payload([view])) > max_bytes
                else [view]
            )
            for selected in prepared:
                if _size(_payload([selected])) > max_bytes:
                    failures[selected.unit.unit_id] = UnitResult(
                        unit_id=selected.unit.unit_id,
                        input_refs=selected.unit.input_refs,
                        status="failed",
                        issues=[
                            "discovered material exceeds extraction limit; no truncation applied"
                        ],
                    )
                else:
                    views.append(selected)
        except Exception as exc:  # noqa: BLE001 - isolate provider/schema failures per unit
            failures[unit.unit_id] = UnitResult(
                unit_id=unit.unit_id,
                input_refs=unit.input_refs,
                status="failed",
                issues=[f"preparation failed: {type(exc).__name__}"],
            )
    packs, pack = [], []
    for view in views:
        if pack and (
            len(pack) >= settings.batch_size or _size(_payload(pack + [view])) > max_bytes
        ):
            packs.append(pack)
            pack = []
        pack.append(view)
    if pack:
        packs.append(pack)
    results = dict(failures)
    for pack in packs:
        expected = [v.unit for v in pack]
        prompt = _dump(_payload(pack))
        errors = []
        preserved = {}
        for attempt in range(settings.correction_retries + 1):
            try:
                candidate = await _run(
                    session, ParseBatchResult, SEMANTICS, prompt, correction=attempt > 0
                )
                errors = validate_result(candidate, expected, index)
                for unit in expected:
                    matches = [u for u in candidate.units if u.unit_id == unit.unit_id]
                    if not validate_result(
                        ParseBatchResult(batch_id=package.package_id, units=matches), [unit], index
                    ):
                        preserved[unit.unit_id] = matches[0]
                if not errors:
                    break
                runs = getattr(session, "runs", [])
                if runs and runs[-1].get("model_retry_prompts", 0) > 0:
                    break
                prompt = _dump(
                    {
                        "input": _payload(pack),
                        "correction": [e.model_dump() for e in errors],
                        "previous": candidate.model_dump(mode="json"),
                    }
                )
                if len(prompt.encode()) > max_bytes:
                    break
            except Exception as exc:  # noqa: BLE001 - isolate provider/schema failures per unit
                errors = [f"extraction failed: {type(exc).__name__}"]
                break  # transport failures are never retried
        for view in pack:
            candidates = [preserved[view.unit.unit_id]] if view.unit.unit_id in preserved else []
            single = ParseBatchResult(batch_id=package.package_id, units=candidates)
            invalid = validate_result(single, [view.unit], index)
            if invalid:
                results[view.unit.unit_id] = UnitResult(
                    unit_id=view.unit.unit_id,
                    input_refs=view.unit.input_refs,
                    status="failed",
                    issues=[str(e) for e in errors] or [str(e) for e in invalid],
                )
            else:
                result = candidates[0]
                if view.issues:
                    result.issues.extend(view.issues)
                    result.status = "partial" if result.offers else "failed"
                results[view.unit.unit_id] = result
    final = []
    for unit in units:
        children = [v for v in views if v.parent_id == unit.unit_id]
        children_ids = {v.unit.unit_id for v in children} | {
            key for key in failures if key.startswith(unit.unit_id + "#")
        }
        if not children_ids:
            final.append(results[unit.unit_id])
            continue
        offers, issues, statuses = [], [], []
        for child_id in sorted(children_ids):
            child = results[child_id]
            statuses.append(child.status)
            issues.extend(child.issues)
            prefix = child_id.split("#")[-1] + ":"
            for source_offer in child.offers:
                offer = copy.deepcopy(source_offer)
                offer.local_id = prefix + offer.local_id
                for item in offer.items:
                    item.local_id = prefix + item.local_id
                for price in offer.prices:
                    price.local_id = prefix + price.local_id
                    price.scope_refs = [prefix + value for value in price.scope_refs]
                for relation in offer.relations:
                    relation.subject_ref = prefix + relation.subject_ref
                    relation.object_ref = prefix + relation.object_ref
                offers.append(offer)
        status = (
            ("partial" if offers else "failed")
            if any(s in ("partial", "failed") for s in statuses)
            else ("complete" if offers else "no_offer")
        )
        final.append(
            UnitResult(
                unit_id=unit.unit_id,
                input_refs=unit.input_refs,
                status=status,
                offers=offers,
                issues=issues,
            )
        )
    return ParseBatchResult(batch_id=package.package_id, units=final)
