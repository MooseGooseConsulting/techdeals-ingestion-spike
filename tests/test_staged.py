import json
from types import SimpleNamespace

import pytest

from techdeals_ingestion.contracts import CaptureInput, ParseBatchResult, UnitResult, WorkPackage
from techdeals_ingestion.evidence import EvidenceIndex
from techdeals_ingestion.staged import Discovery, _chunks, parse_batch


class Session:
    def __init__(self, behavior=None):
        self.calls = []
        self.behavior = behavior

    def build_agent(self, **kwargs):
        return kwargs["output_type"]

    async def run(self, agent, prompt):
        data = json.loads(prompt)
        self.calls.append((agent, data, len(prompt.encode())))
        if self.behavior:
            value = self.behavior(agent, data, len(self.calls))
            if isinstance(value, Exception):
                raise value
            if value is not None:
                return SimpleNamespace(output=value)
        if agent is Discovery:
            return SimpleNamespace(
                output=Discovery(
                    refs=[r["ref"] for r in data["material"]["records"]], disposition="relevant"
                )
            )
        units = data.get("input", data)["units"]
        return SimpleNamespace(
            output=ParseBatchResult(
                batch_id="model-id",
                units=[
                    UnitResult(unit_id=u["unit_id"], input_refs=u["input_refs"], status="no_offer")
                    for u in reversed(units)
                ],
            )
        )


def package(tmp_path, count=1, text="offer material", media="text/plain"):
    captures = []
    for i in range(count):
        path = tmp_path / f"{i}.txt"
        path.write_text(text)
        captures.append(
            CaptureInput(capture_id=f"c{i}", acquisition_key=f"a{i}", path=path, media_type=media)
        )
    p = WorkPackage(package_id="p", captures=captures)
    return p, EvidenceIndex(captures)


@pytest.mark.asyncio
async def test_pack_eight_units_and_reordered_output(tmp_path):
    p, index = package(tmp_path, count=17)
    session = Session()
    result = await parse_batch(p, index, session)
    assert [len(c[1]["units"]) for c in session.calls] == [8, 8, 1]
    assert [u.unit_id for u in result.units] == [f"c{i}" for i in range(17)]
    assert all(c[2] <= 96 * 1024 for c in session.calls)


def test_complete_chunk_coverage():
    material = "α" * 100_000
    chunks = _chunks([{"ref": {"capture_id": "c", "locator": "$"}, "text": material}])
    reconstructed = "".join(r["fragment"] for chunk in chunks for r in chunk)
    assert json.loads(reconstructed)["text"] == material
    assert all(
        len(json.dumps(c, ensure_ascii=False, separators=(",", ":")).encode()) <= 64 * 1024
        for c in chunks
    )


@pytest.mark.asyncio
async def test_missing_unit_only_fails_missing_unit(tmp_path):
    p, index = package(tmp_path, count=2)

    def behavior(agent, data, number):
        units = data.get("input", data)["units"]
        return ParseBatchResult(
            batch_id="x",
            units=[
                UnitResult(
                    unit_id=units[0]["unit_id"],
                    input_refs=units[0]["input_refs"],
                    status="no_offer",
                )
            ],
        )

    session = Session(behavior)
    result = await parse_batch(p, index, session)
    assert [u.status for u in result.units] == ["no_offer", "failed"]
    assert len(session.calls) == 2


@pytest.mark.asyncio
async def test_failed_pack_preserves_other_pack(tmp_path):
    p, index = package(tmp_path, count=9)
    session = Session(
        lambda agent, data, number: RuntimeError("transport") if number == 1 else None
    )
    result = await parse_batch(p, index, session)
    assert [u.status for u in result.units] == ["failed"] * 8 + ["no_offer"]
    assert len(session.calls) == 2


@pytest.mark.asyncio
async def test_large_capture_all_discovery_chunks_examined(tmp_path):
    p, index = package(tmp_path, text="x" * 190_000)
    session = Session()
    result = await parse_batch(p, index, session)
    discoveries = [c for c in session.calls if c[0] is Discovery]
    assert {c[1]["chunk"] for c in discoveries} == set(range(discoveries[0][1]["total_chunks"]))
    assert result.units[0].status == "failed"
    assert "no truncation" in result.units[0].issues[0]


@pytest.mark.asyncio
async def test_linked_capture_accounting(tmp_path):
    p, index = package(tmp_path, count=2)
    for capture in p.captures:
        capture.linked_group = "joint"
    result = await parse_batch(p, index, Session())
    assert len(result.units) == 1
    assert {r.capture_id for r in result.units[0].input_refs} == {"c0", "c1"}


@pytest.mark.asyncio
async def test_correction_preserves_formerly_good_unit(tmp_path):
    p, index = package(tmp_path, count=2)

    def behavior(agent, data, number):
        units = data.get("input", data)["units"]
        unit = units[0 if number == 1 else 1]
        return ParseBatchResult(
            batch_id="x",
            units=[
                UnitResult(
                    unit_id=unit["unit_id"], input_refs=unit["input_refs"], status="no_offer"
                )
            ],
        )

    result = await parse_batch(p, index, Session(behavior))
    assert [u.status for u in result.units] == ["no_offer", "no_offer"]


def test_html_card_selection_includes_descendant_price(tmp_path):
    from techdeals_ingestion.contracts import EvidenceRef, prepare_units
    from techdeals_ingestion.staged import _prepare, _select_records

    p, index = package(
        tmp_path,
        text='<article class="card"><h2>Server</h2><span>$200</span><input hidden value="USD"></article>',
        media="text/html",
    )
    view = _prepare(prepare_units(p)[0], index)
    card = EvidenceRef.model_validate(index.list_children(view.unit.input_refs[0])["refs"][0])
    records = _select_records([card], view, index)
    assert any(r.get("text") == "$200" for r in records)
    assert any(r.get("attrs", {}).get("value") == "USD" for r in records)


@pytest.mark.asyncio
async def test_discovery_groups_pack_and_merge_to_original_unit(tmp_path):
    p, index = package(
        tmp_path,
        text=json.dumps({"products": [{"name": str(i)} for i in range(24)]}),
        media="application/json",
    )

    def behavior(agent, data, number):
        if agent is Discovery:
            return Discovery(
                disposition="relevant",
                groups=[
                    [{"capture_id": "c0", "locator": f"json:/products/{i}"}] for i in range(24)
                ],
            )

    session = Session(behavior)
    result = await parse_batch(p, index, session)
    assert len(session.calls) == 4
    assert [len(call[1]["units"]) for call in session.calls[1:]] == [8, 8, 8]
    assert [u.unit_id for u in result.units] == ["c0"]
    assert result.units[0].status == "no_offer"
