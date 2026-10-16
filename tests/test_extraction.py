import json

import pytest
from pydantic_ai import Agent, ToolOutput
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from test_staged import package

from techdeals_ingestion.contracts import ParseBatchResult, UnitResult, prepare_units
from techdeals_ingestion.extraction import parse_direct, run_validated
from techdeals_ingestion.settings import ParserSettings


class Session:
    settings = ParserSettings.load()

    def __init__(self, respond):
        self.respond = respond
        self.runs = 0
        self.prompts = []

    def build_agent(self, *args, **kwargs):
        return Agent(
            FunctionModel(self.respond), output_type=ToolOutput(ParseBatchResult), retries=1
        )

    async def run(self, agent, prompt, deps=None):
        self.runs += 1
        self.prompts.append(prompt)
        return await agent.run(prompt)


@pytest.mark.asyncio
async def test_sdk_correction_retains_history_and_valid_units(tmp_path):
    p, index = package(tmp_path, count=2)
    units = prepare_units(p)
    histories = []

    def respond(messages, info):
        histories.append(messages)
        unit = units[len(histories) - 1]
        output = ParseBatchResult(
            batch_id="p",
            units=[UnitResult(unit_id=unit.unit_id, input_refs=unit.input_refs, status="no_offer")],
        )
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, output.model_dump())])

    session = Session(respond)
    result = await parse_direct(p, index, session)
    assert [u.status for u in result.units] == ["no_offer", "no_offer"]
    assert session.runs == 1
    assert len(histories) == 2
    assert any(part.part_kind == "retry-prompt" for m in histories[1] for part in m.parts)
    assert any(part.part_kind == "tool-call" for m in histories[1] for part in m.parts)
    data = json.loads(session.prompts[0])
    assert len(data["units"]) == 2
    assert data["units"][0]["captures"][0]["native"] == "offer material"


@pytest.mark.asyncio
async def test_wrong_batch_is_rejected_without_patch(tmp_path):
    p, index = package(tmp_path)
    units = prepare_units(p)
    calls = []

    def respond(messages, info):
        calls.append(1)
        output = ParseBatchResult(
            batch_id="wrong",
            units=[
                UnitResult(
                    unit_id=units[0].unit_id, input_refs=units[0].input_refs, status="no_offer"
                )
            ],
        )
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, output.model_dump())])

    session = Session(respond)
    output = await run_validated(session, session.build_agent(), "extract", units, index, "p")
    assert output.units[0].status == "failed"
    assert len(calls) == 2
    assert session.runs == 1
    assert "batch_id" in output.units[0].issues[0]


@pytest.mark.asyncio
async def test_valid_unit_survives_transport_failure_without_raw_error(tmp_path):
    p, index = package(tmp_path, count=2)
    units = prepare_units(p)
    calls = []

    def respond(messages, info):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("private provider error payload")
        output = ParseBatchResult(
            batch_id="p",
            units=[
                UnitResult(
                    unit_id=units[0].unit_id, input_refs=units[0].input_refs, status="no_offer"
                )
            ],
        )
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, output.model_dump())])

    session = Session(respond)
    output = await parse_direct(p, index, session)
    assert [u.status for u in output.units] == ["no_offer", "failed"]
    assert "private provider" not in output.model_dump_json()
    assert "RuntimeError" in output.units[1].issues[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_status", ["partial", "failed"])
async def test_same_run_correction_can_improve_a_previous_incomplete_unit(tmp_path, initial_status):
    p, index = package(tmp_path, count=2)
    units = prepare_units(p)
    calls = []

    def respond(messages, info):
        calls.append(1)
        output = ParseBatchResult(
            batch_id="p",
            units=[
                UnitResult(
                    unit_id=unit.unit_id,
                    input_refs=unit.input_refs,
                    status=initial_status if len(calls) == 1 else "no_offer",
                )
                for unit in (units[:1] if len(calls) == 1 else units)
            ],
        )
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, output.model_dump())])

    session = Session(respond)
    output = await parse_direct(p, index, session)
    assert [unit.status for unit in output.units] == ["no_offer", "no_offer"]
    assert session.runs == 1 and len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("revision", ["no_offer", "different_price"])
async def test_repair_does_not_rewrite_a_previously_complete_unit(tmp_path, revision):
    from techdeals_ingestion.contracts import Offer, Price

    p, index = package(tmp_path, count=2)
    units = prepare_units(p)
    calls = []

    def respond(messages, info):
        calls.append(1)
        changed = len(calls) > 1
        output_units = [
            UnitResult(
                unit_id=units[0].unit_id,
                input_refs=units[0].input_refs,
                status="no_offer" if changed and revision == "no_offer" else "complete",
                offers=[]
                if changed and revision == "no_offer"
                else [
                    Offer(
                        local_id="offer",
                        evidence=units[0].input_refs,
                        prices=[
                            Price(
                                local_id="price",
                                amount="20" if changed else "10",
                                kind="ask",
                                scope_refs=["offer"],
                                evidence=units[0].input_refs,
                            )
                        ],
                    )
                ],
            )
        ]
        if changed:
            output_units.append(
                UnitResult(
                    unit_id=units[1].unit_id,
                    input_refs=units[1].input_refs,
                    status="no_offer",
                )
            )
        output = ParseBatchResult(batch_id="p", units=output_units)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, output.model_dump())])

    session = Session(respond)
    output = await parse_direct(p, index, session)
    assert output.units[0].status == "complete"
    assert output.units[0].offers[0].prices[0].amount == 10
    assert output.units[1].status == "no_offer"
    assert session.runs == 1 and len(calls) == 2
