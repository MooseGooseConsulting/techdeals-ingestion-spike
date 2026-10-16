"""A package-scoped native-evidence reader and typed, multi-unit parser."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import RunContext

from .contracts import EvidenceRef, ParseBatchResult, UnitResult, WorkPackage, prepare_units
from .evidence import EvidenceIndex
from .instructions import SEMANTIC_INSTRUCTIONS
from .validation import validate_result

RESPONSE_BYTES = 16_384
EXPOSURE_BYTES = 512 * 1024
TOOL_ATTEMPTS = 64


@dataclass
class Reader:
    """The only capability passed to model tools; never opens paths."""

    index: EvidenceIndex
    allowed: frozenset[str]
    attempts: int = 0
    max_attempts: int = TOOL_ATTEMPTS
    page_bytes: int = RESPONSE_BYTES
    exposed: dict[tuple[str, str], set[int]] = field(default_factory=dict)
    exposure_bytes: int = 0

    def begin(self) -> None:
        self.attempts += 1
        if self.attempts > self.max_attempts:
            raise ValueError("package tool attempt limit exhausted")

    def check(self, ref: EvidenceRef) -> None:
        if ref.capture_id not in self.allowed:
            raise ValueError("capture is outside this work package")
        self.index.resolve(ref)

    def finish(self, result: dict) -> dict:
        if len(json.dumps(result, ensure_ascii=False).encode()) > self.page_bytes:
            raise ValueError("serialized tool response exceeds 16 KiB; request a smaller page")
        return result

    def read(self, ref: EvidenceRef, offset: int, limit: int) -> dict:
        self.begin()
        self.check(ref)
        page = self.index.read(ref, offset, limit)
        # Escape expansion and metadata count against the transport cap. Shrink by
        # byte boundary using EvidenceIndex rather than slicing Unicode strings.
        while len(json.dumps(page, ensure_ascii=False).encode()) > self.page_bytes:
            limit //= 2
            if limit < 4:
                raise ValueError("reference metadata exceeds response limit")
            page = self.index.read(ref, offset, limit)
        end = offset + len(page["text"].encode())
        key = (ref.capture_id, ref.locator)
        prior = self.exposed.get(key, set())
        fresh = set(range(offset, end)) - prior
        self.exposed.setdefault(key, set()).update(fresh)
        self.exposure_bytes += len(fresh)
        return self.finish(page)

    def children(self, ref: EvidenceRef, offset: int, limit: int) -> dict:
        self.begin()
        self.check(ref)
        return self.finish(self.index.list_children(ref, offset, limit))

    def search(self, capture_id: str, query: str, offset: int, limit: int) -> dict:
        self.begin()
        self.check(EvidenceRef(capture_id=capture_id, locator="$"))
        return self.finish(self.index.search(query, [capture_id], offset, limit))

    def fully_read(self, capture_id: str) -> bool:
        # Only an exhaustive root read proves complete coverage without guessing
        # which nested native nodes overlap or omit source material.
        return len(self.exposed.get((capture_id, "$"), ())) == len(
            self.index.resolve(EvidenceRef(capture_id=capture_id, locator="$")).encode()
        )


async def list_children(
    ctx: RunContext[Reader], ref: EvidenceRef, offset: int = 0, limit: int = 50
) -> dict:
    """List native child references; follow next_offset explicitly for more."""
    try:
        return ctx.deps.children(ref, offset, limit)
    except (ValueError, KeyError) as exc:
        return {"error": str(exc)[:1000]}


async def read_region(
    ctx: RunContext[Reader], ref: EvidenceRef, offset: int = 0, limit: int = 12_000
) -> dict:
    """Read native evidence bytes. Follow next_offset until null for full coverage."""
    try:
        return ctx.deps.read(ref, offset, limit)
    except (ValueError, KeyError) as exc:
        return {"error": str(exc)[:1000]}


async def search_capture(
    ctx: RunContext[Reader], capture_id: str, query: str, offset: int = 0, limit: int = 50
) -> dict:
    """Search one allowed capture; matches identify regions, not read evidence."""
    try:
        return ctx.deps.search(capture_id, query, offset, limit)
    except (ValueError, KeyError) as exc:
        return {"error": str(exc)[:1000]}


INSTRUCTIONS = (
    SEMANTIC_INSTRUCTIONS
    + """
Use only supplied tools to inspect this package's native captures. Read relevant
material before submitting; listing/searching references does not read bodies.
Choose regions with list_children/search_capture and read_region, following explicit
next_offset pagination. Return one typed UnitResult per supplied unit, preserving
linked captures together. Report incomplete coverage honestly. Native byte coverage
is an audit measure, not proof of semantic completeness. Cite evidence actually
read through tools. Return the supplied package_id as batch_id.
"""
)


def _read_any(reader: Reader, capture_id: str) -> bool:
    return any(cid == capture_id and offsets for (cid, _), offsets in reader.exposed.items())


async def parse_batch(package: WorkPackage, index: EvidenceIndex, session: Any) -> ParseBatchResult:
    units = prepare_units(package)
    settings = getattr(session, "settings", None)
    reader = Reader(
        index,
        frozenset(c.capture_id for c in package.captures),
        max_attempts=getattr(settings, "max_tool_calls", TOOL_ATTEMPTS),
        page_bytes=min(getattr(settings, "tool_page_bytes", RESPONSE_BYTES), RESPONSE_BYTES),
    )
    native_bytes = sum(len(index.originals[c.capture_id]) for c in package.captures)
    native_limit = getattr(settings, "max_package_bytes", EXPOSURE_BYTES)
    if native_bytes > native_limit:
        return ParseBatchResult(
            batch_id=package.package_id,
            units=[
                UnitResult(
                    unit_id=u.unit_id,
                    input_refs=u.input_refs,
                    status="failed",
                    issues=[f"Native package bytes {native_bytes} exceed limit {native_limit}"],
                )
                for u in units
            ],
        )
    summary = [
        {
            **c.model_dump(mode="json", exclude={"path"}),
            "original_bytes": len(index.originals[c.capture_id]),
        }
        for c in package.captures
    ]
    context = json.dumps(
        {
            "package_id": package.package_id,
            "units": [u.model_dump() for u in units],
            "captures": summary,
        },
        ensure_ascii=False,
    )
    prompt = context
    agent = session.build_agent(
        output_type=ParseBatchResult,
        instructions=INSTRUCTIONS,
        tools=[list_children, read_region, search_capture],
        deps_type=Reader,
    )
    accepted = {}
    errors = []
    for attempt in range(2):
        try:
            response = await session.run(agent, prompt, deps=reader)
            result = response.output
            errors = validate_result(result, units, index)
            if result.batch_id != package.package_id:
                errors.append("batch_id must match package_id")
            for expected in units:
                candidates = [u for u in result.units if u.unit_id == expected.unit_id]
                if expected.unit_id in accepted or len(candidates) != 1:
                    continue
                unit = candidates[0]
                local_errors = validate_result(
                    ParseBatchResult(batch_id=package.package_id, units=[unit]), [expected], index
                )
                if not local_errors:
                    accepted[unit.unit_id] = unit
            if len(accepted) == len(units):
                break
            prompt = (
                context
                + "\nCorrect only missing or invalid units once. Already accepted: "
                + str(list(accepted))
                + "\nValidation errors: "
                + str(errors)
                + "\nPrior result: "
                + result.model_dump_json()
            )
        except Exception as exc:  # noqa: BLE001 - provider failures become explicit unit failures
            errors = [f"{type(exc).__name__}: {exc}"]
            break
    final = []
    for expected in units:
        unit = accepted.get(expected.unit_id)
        if unit is None:
            unit = UnitResult(
                unit_id=expected.unit_id,
                input_refs=expected.input_refs,
                status="failed",
                issues=["Invalid or missing model unit", *map(str, errors)],
            )
        elif unit.status in ("complete", "no_offer") and not all(
            _read_any(reader, r.capture_id) for r in expected.input_refs
        ):
            unit = unit.model_copy(
                update={
                    "status": "partial",
                    "issues": [
                        *unit.issues,
                        "One or more captures had no evidence bytes read through tools",
                    ],
                }
            )
        final.append(unit)
    session.coverage = {
        "native_package_bytes": native_bytes,
        "unique_view_bytes_read": reader.exposure_bytes,
        "tool_body_attempts": reader.attempts,
        "captures": [
            {
                "capture_id": cid,
                "root_fully_read": reader.fully_read(cid),
                "any_view_read": bool(_read_any(reader, cid)),
            }
            for cid in sorted(reader.allowed)
        ],
    }
    return ParseBatchResult(batch_id=package.package_id, units=final)
