import json

import pytest
from pydantic_ai import Agent, ToolOutput
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from techdeals_ingestion.contracts import CaptureInput, ParseBatchResult, UnitResult, WorkPackage
from techdeals_ingestion.evidence import EvidenceIndex
from techdeals_ingestion.staged import Discovery, _chunks, parse_batch


class Session:
    def __init__(self, behavior=None):
        self.calls = []
        self.behavior = behavior

    def build_agent(self, **kwargs):
        output_type = kwargs["output_type"]

        def respond(messages, info):
            prompt = next(
                p.content for m in messages for p in m.parts if p.part_kind == "user-prompt"
            )
            value = self.output(output_type, prompt)
            return ModelResponse(
                parts=[ToolCallPart(info.output_tools[0].name, value.model_dump(mode="json"))]
            )

        return Agent(FunctionModel(respond), output_type=ToolOutput(output_type), retries=1)

    async def run(self, agent, prompt, deps=None):
        return await agent.run(prompt)

    def output(self, agent, prompt):

        data = json.loads(prompt)
        self.calls.append((agent, data, len(prompt.encode())))
        if self.behavior:
            value = self.behavior(agent, data, len(self.calls))
            if isinstance(value, Exception):
                raise value
            if value is not None:
                return value
        if agent is Discovery:
            return Discovery(
                refs=[
                    {"capture_id": c["capture_id"], "locator": "$"}
                    for c in data["overview"].get("captures", [data["overview"]])
                ],
                disposition="relevant",
            )
        units = data.get("input", data)["units"]
        return ParseBatchResult(
            batch_id=data.get("batch_id", "p"),
            units=[
                UnitResult(unit_id=u["unit_id"], input_refs=u["input_refs"], status="no_offer")
                for u in reversed(units)
            ],
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
            batch_id="p",
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
async def test_large_capture_complete_overview_and_untruncated_root_selection(tmp_path):
    p, index = package(tmp_path, text="x" * 190_000)
    session = Session()
    result = await parse_batch(p, index, session)
    discoveries = [c for c in session.calls if c[0] is Discovery]
    assert len(discoveries) == 1
    capture = discoveries[0][1]["overview"]["captures"][0]
    assert capture["inventory"] == ["$"]
    assert capture["summaries"][0][1] == 190_000
    assert index.originals["c0"] == b"x" * 190_000
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
            batch_id="p",
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
        text=json.dumps({"products": [{"name": str(i), "padding": "x" * 4500} for i in range(24)]}),
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


@pytest.mark.asyncio
async def test_group_merge_prefixes_ids_and_remaps_price_scope(tmp_path):
    from techdeals_ingestion.contracts import Offer, Price, prepare_units
    from techdeals_ingestion.validation import validate_result

    p, index = package(
        tmp_path,
        text=json.dumps(
            [{"name": "A", "padding": "x" * 50000}, {"name": "B", "padding": "x" * 50000}]
        ),
        media="application/json",
    )

    def behavior(agent, data, number):
        if agent is Discovery:
            return Discovery(
                disposition="relevant",
                groups=[[{"capture_id": "c0", "locator": f"json:/{i}"}] for i in range(2)],
            )
        return ParseBatchResult(
            batch_id="p",
            units=[
                UnitResult(
                    unit_id=u["unit_id"],
                    input_refs=u["input_refs"],
                    status="complete",
                    offers=[
                        Offer(
                            local_id="offer",
                            evidence=u["input_refs"],
                            prices=[
                                Price(
                                    local_id="price",
                                    kind="ask",
                                    amount="10",
                                    scope_refs=["offer"],
                                    evidence=u["input_refs"],
                                )
                            ],
                        )
                    ],
                )
                for u in data["units"]
            ],
        )

    result = await parse_batch(p, index, Session(behavior))
    offers = result.units[0].offers
    assert len(offers) == 2
    assert len({o.local_id for o in offers}) == 2
    assert all(o.prices[0].scope_refs == [o.local_id] for o in offers)
    assert validate_result(result, prepare_units(p), index) == []


def test_root_selection_includes_complete_original_not_metadata_only(tmp_path):
    from techdeals_ingestion.contracts import EvidenceRef, prepare_units
    from techdeals_ingestion.staged import _prepare, _select_records

    text = "<html><body><p>" + "z" * 20000 + "</p></body></html>"
    p, index = package(tmp_path, text=text, media="text/html")
    view = _prepare(prepare_units(p)[0], index)
    selected = _select_records([EvidenceRef(capture_id="c0", locator="$")], view, index)
    assert [r["text"] for r in selected if "text" in r] == [text]


def test_actual_development_overviews_fit_single_discovery_request():
    from pathlib import Path

    from techdeals_ingestion.contracts import prepare_units
    from techdeals_ingestion.evidence import load_work_packages
    from techdeals_ingestion.staged import _overview, _overview_pages, _prepare, _size

    # Fixed development source inputs only; oracle and held-back source bytes are never read.
    manifest = Path(__file__).parents[1] / "corpus/manifest.json"
    sizes = {}
    for p in load_work_packages(manifest):
        if p.package_id not in ("dev-a", "dev-b"):
            continue
        index = EvidenceIndex(p.captures)
        for unit in prepare_units(p):
            overview = _overview(_prepare(unit, index), index)
            pages = _overview_pages(overview, 96 * 1024 - 4096)
            assert len(pages) == 1, unit.unit_id
            assert _size(pages[0]) < 96 * 1024 - 4096
            sizes[unit.unit_id] = _size(pages[0])
            for capture in overview["captures"]:
                inventory = capture["inventory"]
                if isinstance(inventory, dict) and "html_preorder_range_inclusive" in inventory:
                    low, high = inventory["html_preorder_range_inclusive"]
                    actual = [
                        r["locator"]
                        for r in index.snapshot([capture["capture_id"]])[0]["refs"]
                        if r["locator"].startswith("html:@")
                    ]
                    assert {f"html:@{i}" for i in range(low, high + 1)} == set(actual)
                elif isinstance(inventory, dict):
                    assert {row[0] for row in capture["summaries"]} == {
                        r["locator"]
                        for r in index.snapshot([capture["capture_id"]])[0]["refs"]
                        if r["locator"].startswith("json:")
                    }
    assert "disctech" in sizes and "spd" in sizes


@pytest.mark.asyncio
async def test_cumulative_prepared_exposure_stops_later_pack_before_request(tmp_path):
    from techdeals_ingestion.settings import ParserSettings

    p, index = package(tmp_path, count=2, text="x" * 1000)
    session = Session()
    session.settings = ParserSettings.load().model_copy(
        update={"max_exposure_bytes": 1800, "batch_size": 1}
    )
    result = await parse_batch(p, index, session)
    assert len(session.calls) == 1
    assert [u.status for u in result.units] == ["no_offer", "failed"]
    assert "exposure limit" in result.units[1].issues[0]
    assert session.coverage["unique_prepared_view_bytes"] <= 1800


def test_prepared_budget_counts_repeated_views_and_discovery_previews():
    from techdeals_ingestion.staged import ExposureBudget

    budget = ExposureBudget(100)
    preview = {"text_preview": "α"}
    budget.reserve([preview])
    size = budget.unique_bytes
    budget.reserve([preview])
    assert budget.unique_bytes == size
    assert budget.report()["repeated_prepared_view_bytes"] == size
    with pytest.raises(ValueError, match="exposure limit"):
        budget.reserve([{"text": "x" * 100}])
    assert budget.total_bytes == size * 2


@pytest.mark.asyncio
async def test_batch_id_envelope_is_counted_when_splitting_packs(tmp_path):
    from techdeals_ingestion.contracts import prepare_units
    from techdeals_ingestion.settings import ParserSettings
    from techdeals_ingestion.staged import _payload, _prepare, _size

    p, index = package(tmp_path, count=2, text="x" * 2000)
    p.package_id = "batch-" + "x" * 500
    views = [_prepare(unit, index) for unit in prepare_units(p)]
    full = _payload(views, p.package_id)
    without_id = {key: value for key, value in full.items() if key != "batch_id"}
    limit = _size(without_id) + 1
    assert _size(full) > limit
    assert all(_size(_payload([view], p.package_id)) <= limit for view in views)
    session = Session()
    session.settings = ParserSettings.load().model_copy(update={"max_prepared_bytes": limit})
    result = await parse_batch(p, index, session)
    assert [len(call[1]["units"]) for call in session.calls] == [1, 1]
    assert all(call[2] <= limit for call in session.calls)
    assert all(unit.status == "no_offer" for unit in result.units)


@pytest.mark.asyncio
async def test_single_unit_over_limit_with_envelope_fails_before_extraction(tmp_path, monkeypatch):
    from techdeals_ingestion.contracts import prepare_units
    from techdeals_ingestion.settings import ParserSettings
    from techdeals_ingestion.staged import _payload, _prepare, _size

    p, index = package(tmp_path)
    p.package_id = "batch-" + "x" * 500
    view = _prepare(prepare_units(p)[0], index)
    full = _payload([view], p.package_id)
    limit = _size({key: value for key, value in full.items() if key != "batch_id"}) + 1
    discoveries = []

    async def discover(view, index, session, exposure):
        discoveries.append(view)
        return [view]

    monkeypatch.setattr("techdeals_ingestion.staged._discover", discover)
    session = Session()
    session.settings = ParserSettings.load().model_copy(update={"max_prepared_bytes": limit})
    result = await parse_batch(p, index, session)
    assert len(discoveries) == 1
    assert session.calls == []
    assert result.units[0].status == "failed"
    assert "exceeds extraction limit; no truncation" in result.units[0].issues[0]
