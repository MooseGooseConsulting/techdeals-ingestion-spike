import json
from types import SimpleNamespace

import pytest

from techdeals_ingestion.contracts import (
    CaptureInput,
    EvidenceRef,
    ParseBatchResult,
    UnitResult,
    WorkPackage,
)
from techdeals_ingestion.evidence import EvidenceIndex
from techdeals_ingestion.tool_parser import Reader, parse_batch


def setup(tmp_path, text="offer $50"):
    captures = []
    for name in ("a", "b"):
        path = tmp_path / name
        path.write_text(text)
        captures.append(
            CaptureInput(capture_id=name, acquisition_key=name, path=path, media_type="text/plain")
        )
    return WorkPackage(package_id="batch", captures=captures), EvidenceIndex(captures)


def test_scope_attempts_and_pagination(tmp_path):
    _package, index = setup(tmp_path, '"' * 30_000)
    reader = Reader(index, frozenset(["a"]))
    ref = EvidenceRef(capture_id="a", locator="$")
    page = reader.read(ref, 0, 16_384)
    assert len(json.dumps(page, ensure_ascii=False).encode()) <= 16_384
    assert page["next_offset"] is not None
    assert reader.read(ref, page["next_offset"], 100)["offset"] == page["next_offset"]
    prior = reader.exposure_bytes
    reader.read(ref, 0, 100)
    assert reader.exposure_bytes == prior
    for _ in range(61):
        with pytest.raises(ValueError):
            reader.read(EvidenceRef(capture_id="b", locator="$"), 0, 100)
    with pytest.raises(ValueError, match="attempt limit"):
        reader.read(ref, 0, 100)
    assert reader.attempts == 65


class Session:
    def __init__(self, fail=False, read=True):
        self.fail = fail
        self.read = read

    def build_agent(self, **kwargs):
        assert len(kwargs["tools"]) == 3
        return kwargs

    async def run(self, agent, prompt, deps):
        if self.fail:
            raise RuntimeError("provider failed")
        if self.read:
            for cid in deps.allowed:
                deps.read(EvidenceRef(capture_id=cid, locator="$"), 0, 100)
        data = json.loads(prompt)
        return SimpleNamespace(
            output=ParseBatchResult(
                batch_id=data["package_id"],
                units=[
                    UnitResult(unit_id=u["unit_id"], input_refs=u["input_refs"], status="no_offer")
                    for u in data["units"]
                ],
            )
        )


@pytest.mark.asyncio
async def test_typed_multiunit_and_failure_accounting(tmp_path):
    package, index = setup(tmp_path)
    result = await parse_batch(package, index, Session())
    assert len(result.units) == 2
    assert all(u.status == "no_offer" for u in result.units)
    failed = await parse_batch(package, index, Session(fail=True))
    assert len(failed.units) == 2
    assert all(u.status == "failed" and u.issues for u in failed.units)
    unread = await parse_batch(package, index, Session(read=False))
    assert all(u.status == "partial" for u in unread.units)
