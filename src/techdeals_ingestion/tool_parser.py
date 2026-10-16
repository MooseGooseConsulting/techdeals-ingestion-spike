"""A package-scoped native-evidence reader and typed, multi-unit parser."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Annotated, Any

from pydantic import Field
from pydantic_ai import RunContext, Tool

from .contracts import EvidenceRef, ParseBatchResult, UnitResult, WorkPackage, prepare_units
from .evidence import EvidenceIndex
from .instructions import SEMANTIC_INSTRUCTIONS
from .validation import validate_result

RESPONSE_BYTES = 16_384
EXPOSURE_BYTES = 512 * 1024
TOOL_ATTEMPTS = 64

Offset = Annotated[int, Field(ge=0)]
ByteLimit = Annotated[int, Field(ge=1, le=RESPONSE_BYTES)]
IndexLimit = Annotated[int, Field(ge=1, le=100)]
CaptureIDs = Annotated[list[str], Field(min_length=1, max_length=8)]
Locators = Annotated[list[str], Field(min_length=1, max_length=8)]


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
    session: Any = None
    max_requests: int = 12

    def begin(self) -> None:
        self.attempts += 1
        if self.attempts > self.max_attempts:
            raise ValueError("package tool attempt limit exhausted")

    def check(self, ref: EvidenceRef) -> None:
        if ref.capture_id not in self.allowed:
            raise ValueError("capture is outside this work package")
        self.index.resolve(ref)

    def remaining_requests(self) -> int:
        return max(
            0, self.max_requests - getattr(getattr(self.session, "usage", None), "requests", 0)
        )

    def envelope(self, result: dict) -> dict:
        return {**result, "remaining_model_requests": self.remaining_requests()}

    def finish(self, result: dict) -> dict:
        result = self.envelope(result)
        if len(json.dumps(result, ensure_ascii=False).encode()) > self.page_bytes:
            raise ValueError("serialized tool response exceeds 16 KiB; request a smaller page")
        return result

    def record(self, page: dict) -> None:
        offset = page["offset"]
        end = offset + len(page["text"].encode())
        key = (page["ref"]["capture_id"], page["ref"]["locator"])
        prior = self.exposed.get(key, set())
        fresh = set(range(offset, end)) - prior
        self.exposed.setdefault(key, set()).update(fresh)
        self.exposure_bytes += len(fresh)

    def read(self, ref: EvidenceRef, offset: int, limit: int) -> dict:
        result = self.read_many([ref.capture_id], [ref.locator], [offset], limit)
        return self.finish(result["pages"][0])

    def read_many(
        self, capture_ids: list[str], locators: list[str], offsets: list[int] | None, limit: int
    ) -> dict:
        self.begin()
        if not 1 <= len(capture_ids) <= 8 or len(capture_ids) != len(locators):
            raise ValueError("capture_ids and locators must have equal lengths in 1..8")
        offsets = [0] * len(capture_ids) if offsets is None else offsets
        if len(offsets) != len(capture_ids):
            raise ValueError("offsets must have the same length as capture_ids")
        if not 1 <= limit <= RESPONSE_BYTES or any(offset < 0 for offset in offsets):
            raise ValueError("offset must be >=0; limit must be in 1..16384 bytes")
        refs = [EvidenceRef(capture_id=cid, locator=loc) for cid, loc in zip(capture_ids, locators)]
        for ref in refs:
            self.check(ref)
        # Shrink every requested page together until the combined transport fits.
        # Every reference remains present, with its own explicit continuation.
        while True:
            pages = [self.index.read(ref, offset, limit) for ref, offset in zip(refs, offsets)]
            result = {"pages": pages}
            if (
                len(json.dumps(self.envelope(result), ensure_ascii=False).encode())
                <= self.page_bytes
            ):
                break
            if limit <= 4:
                raise ValueError("reference metadata exceeds response limit; request fewer regions")
            limit = max(4, limit // 2)
        result = self.finish(result)
        for page in pages:
            self.record(page)
        return result

    def overview(self, capture_id: str) -> dict:
        """Bounded syntax-only index, never source bodies or interpreted fields."""

        def children(locator: str) -> dict:
            page = self.index.list_children(
                EvidenceRef(capture_id=capture_id, locator=locator), 0, 100
            )
            return {
                **page,
                "refs": [
                    {
                        "locator": ref["locator"],
                        "bytes": len(self.index.resolve(EvidenceRef(**ref)).encode()),
                    }
                    for ref in page["refs"]
                ],
            }

        root = children("$")
        return {
            "root_children": root,
            "child_indexes": [
                {"parent_locator": ref["locator"], **children(ref["locator"])}
                for ref in root["refs"]
            ],
            "index_only_not_read": True,
        }

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
    ctx: RunContext[Reader],
    capture_id: str,
    locator: str = "$",
    offset: Offset = 0,
    limit: IndexLimit = 50,
) -> dict:
    """List native child references; follow next_offset explicitly for more."""
    try:
        return ctx.deps.children(EvidenceRef(capture_id=capture_id, locator=locator), offset, limit)
    except (ValueError, KeyError) as exc:
        return ctx.deps.finish(
            {"error": str(exc)[:1000] + "; offsets >=0, byte limits 1..16384, index limits 1..100"}
        )


async def read_region(
    ctx: RunContext[Reader],
    capture_id: str,
    locator: str = "$",
    offset: Offset = 0,
    limit: ByteLimit = 12_000,
) -> dict:
    """Read 1..16384 native bytes at offset >=0; follow next_offset for more."""
    try:
        return ctx.deps.read(EvidenceRef(capture_id=capture_id, locator=locator), offset, limit)
    except (ValueError, KeyError) as exc:
        return ctx.deps.finish(
            {"error": str(exc)[:1000] + "; offsets >=0, byte limits 1..16384, index limits 1..100"}
        )


async def search_capture(
    ctx: RunContext[Reader], capture_id: str, query: str, offset: Offset = 0, limit: IndexLimit = 50
) -> dict:
    """Search one allowed capture; matches identify regions, not read evidence."""
    try:
        return ctx.deps.search(capture_id, query, offset, limit)
    except (ValueError, KeyError) as exc:
        return ctx.deps.finish(
            {"error": str(exc)[:1000] + "; offsets >=0, byte limits 1..16384, index limits 1..100"}
        )


async def read_regions(
    ctx: RunContext[Reader],
    capture_ids: CaptureIDs,
    locators: Locators,
    offsets: Annotated[list[Offset], Field(min_length=1, max_length=8)] | None = None,
    per_region_limit: ByteLimit = 1024,
) -> dict:
    """Read up to eight native regions together using equal-length parallel arrays.

    Omit offsets to start every region at zero. Each page includes next_offset;
    the combined response stays within 16 KiB by reducing page sizes explicitly.
    """
    try:
        return ctx.deps.read_many(capture_ids, locators, offsets, per_region_limit)
    except (ValueError, KeyError) as exc:
        return ctx.deps.finish({"error": str(exc)[:1000]})


async def prepare_reader_tool(ctx: RunContext[Reader], definition):
    """Reserve the final allowed model request for the typed result tool."""
    if ctx.usage.requests >= ctx.deps.max_requests - 1:
        return None
    return definition


def reader_tools() -> list[Tool]:
    return [
        Tool(function, prepare=prepare_reader_tool)
        for function in (list_children, read_region, search_capture, read_regions)
    ]


INSTRUCTIONS = (
    SEMANTIC_INSTRUCTIONS
    + """
Use only supplied tools to inspect this package's native captures. Read relevant
material before submitting; listing/searching references does not read bodies.
The initial compact index contains native references and byte sizes, not read
bodies. Prefer indexed regions over serial byte-paging whole raw markup. Use
list_children/search_capture to locate relevant regions, then read_regions to
pack up to eight regions across captures/units into one response. Read all supplied
captures needed for each unit, preserving native context and following next_offset
when a relevant region continues. Do not spend the whole budget on the first capture.
You have at most 12 total model requests, including the final typed output; the
supplied request_budget may be lower. Every
tool result reports remaining_model_requests. Reserve the last request for
final_result; inspection tools are removed on that last request. Submit honest
partial/failed units for material you could not inspect and preserve usable units. Return one typed UnitResult per supplied unit, preserving
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
        session=session,
        max_requests=getattr(settings, "max_requests", 12),
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
            "native_index": reader.overview(c.capture_id),
        }
        for c in package.captures
    ]
    context = json.dumps(
        {
            "package_id": package.package_id,
            "request_budget": reader.max_requests,
            "units": [u.model_dump() for u in units],
            "captures": summary,
        },
        ensure_ascii=False,
    )
    prompt = context
    agent = session.build_agent(
        output_type=ParseBatchResult,
        instructions=INSTRUCTIONS,
        tools=reader_tools(),
        deps_type=Reader,
    )
    accepted = {}
    errors = []
    for attempt in range(2):
        if attempt:
            runs = getattr(session, "runs", [])
            if runs and runs[-1].get("model_retry_prompts", 0):
                break
            agent = session.build_agent(
                output_type=ParseBatchResult,
                instructions=INSTRUCTIONS,
                tools=reader_tools(),
                deps_type=Reader,
                retry_budget=0,
            )
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
