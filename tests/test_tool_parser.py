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
        assert len(kwargs["tools"]) == 4
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


@pytest.mark.asyncio
async def test_child_reads_allow_complete_and_metadata_survives_repair(tmp_path):
    package, index = setup(tmp_path, '{"title": "A"}')
    package.captures[0].metadata = {"provenance": {"source": "trusted collection"}}
    for capture in package.captures:
        capture.media_type = "application/json"
    index = EvidenceIndex(package.captures)

    class RepairSession(Session):
        calls = 0

        async def run(self, agent, prompt, deps):
            assert "trusted collection" in prompt
            assert str(tmp_path) not in prompt
            self.calls += 1
            cid = "a" if self.calls == 1 else "b"
            deps.read(EvidenceRef(capture_id=cid, locator="json:/title"), 0, 100)
            return SimpleNamespace(
                output=ParseBatchResult(
                    batch_id="batch",
                    units=[
                        UnitResult(
                            unit_id=cid,
                            input_refs=[EvidenceRef(capture_id=cid, locator="$")],
                            status="complete",
                        )
                    ],
                )
            )

    session = RepairSession()
    result = await parse_batch(package, index, session)
    assert [u.status for u in result.units] == ["complete", "complete"]
    assert session.calls == 2
    assert not any(c["root_fully_read"] for c in session.coverage["captures"])
    assert session.coverage["native_package_bytes"] == 28


async def test_real_sdk_reader_uses_flat_tool_arguments(monkeypatch, tmp_path):
    import httpx2 as httpx

    from techdeals_ingestion.provider import LLMRun
    from techdeals_ingestion.settings import ParserSettings

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-secret")
    package, index = setup(tmp_path)
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        number = len(requests)
        if number < 3:
            name = "read_region"
            arguments = {"capture_id": "a" if number == 1 else "b", "locator": "$"}
        else:
            name = "final_result"
            arguments = {
                "batch_id": "batch",
                "units": [
                    {
                        "unit_id": c.capture_id,
                        "input_refs": [{"capture_id": c.capture_id, "locator": "$"}],
                        "status": "no_offer",
                        "offers": [],
                        "issues": [],
                    }
                    for c in package.captures
                ],
            }
        return httpx.Response(
            200,
            json={
                "id": "test",
                "provider": "test",
                "object": "chat.completion",
                "created": 1,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": str(number),
                                    "type": "function",
                                    "function": {"name": name, "arguments": json.dumps(arguments)},
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    async with LLMRun(ParserSettings.load(), http_client=client) as session:
        output = await parse_batch(package, index, session)
        assert all(u.status == "no_offer" for u in output.units), str(
            [u.issues for u in output.units]
        )
        assert session.usage.tool_calls == 2
    tools = {t["function"]["name"]: t["function"] for t in requests[0]["tools"]}
    for name in ("read_region", "list_children"):
        properties = tools[name]["parameters"]["properties"]
        assert properties["capture_id"]["type"] == "string"
        assert properties["locator"]["type"] == "string"
        assert "ref" not in properties
    assert "offer $50" in json.dumps(requests[2]["messages"])


def test_batch_pages_preserve_all_references_and_scope(tmp_path):
    _package, index = setup(tmp_path, '"' * 30_000)
    reader = Reader(index, frozenset(["a", "b"]))
    page = reader.read_many(["a", "b"], ["$", "$"], None, 16_384)
    assert len(json.dumps(page, ensure_ascii=False).encode()) <= 16_384
    assert [p["ref"]["capture_id"] for p in page["pages"]] == ["a", "b"]
    assert all(p["next_offset"] is not None for p in page["pages"])
    assert page["remaining_model_requests"] == 12
    full_batch = reader.read_many(["a", "b"] * 4, ["$"] * 8, None, 16_384)
    assert len(full_batch["pages"]) == 8
    assert len(json.dumps(full_batch, ensure_ascii=False).encode()) <= 16_384
    assert all(p["next_offset"] is not None for p in full_batch["pages"])
    first_end = [p["next_offset"] for p in page["pages"]]
    continuation = reader.read_many(["a", "b"], ["$", "$"], first_end, 1024)
    assert [p["offset"] for p in continuation["pages"]] == first_end
    before = reader.exposure_bytes
    with pytest.raises(ValueError, match="outside this work package"):
        reader.read_many(["a", "secret"], ["$", "$"], None, 1024)
    assert reader.exposure_bytes == before
    with pytest.raises(ValueError, match="equal lengths"):
        reader.read_many(["a", "b"], ["$"], None, 1024)
    assert reader.attempts == 5


@pytest.mark.asyncio
async def test_sdk_batch_reads_and_reserves_final_request(monkeypatch, tmp_path):
    import httpx2 as httpx

    from techdeals_ingestion.provider import LLMRun
    from techdeals_ingestion.settings import ParserSettings

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-secret")
    package, index = setup(tmp_path, '{"title": "native evidence"}')
    for capture in package.captures:
        capture.media_type = "application/json"
    index = EvidenceIndex(package.captures)
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        number = len(requests)
        if number == 1:
            name = "read_regions"
            arguments = {"capture_ids": ["a", "b"], "locators": ["json:/title", "json:/title"]}
        elif number == 2:
            name = "read_region"
            arguments = {"capture_id": "a", "locator": "json:/title"}
        else:
            name = "final_result"
            arguments = {
                "batch_id": "batch",
                "units": [
                    UnitResult(
                        unit_id=cid,
                        input_refs=[EvidenceRef(capture_id=cid, locator="$")],
                        status="no_offer",
                    ).model_dump(mode="json")
                    for cid in ("a", "b")
                ],
            }
        return httpx.Response(
            200,
            json={
                "id": "batch-test",
                "provider": "test",
                "object": "chat.completion",
                "created": 1,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": str(number),
                                    "type": "function",
                                    "function": {"name": name, "arguments": json.dumps(arguments)},
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )

    settings = ParserSettings.load().model_copy(update={"max_requests": 3})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    async with LLMRun(settings, http_client=client) as session:
        output = await parse_batch(package, index, session)
        assert [u.status for u in output.units] == ["no_offer", "no_offer"]
        assert session.coverage["unique_view_bytes_read"] == 2 * len('"native evidence"')
        assert session.usage.requests == 3
        assert session.coverage["tool_body_attempts"] == 2
    tools = {t["function"]["name"]: t["function"] for t in requests[0]["tools"]}
    byte_schema = tools["read_region"]["parameters"]["properties"]["limit"]
    assert (byte_schema["minimum"], byte_schema["maximum"]) == (1, 16384)
    batch_schema = tools["read_regions"]["parameters"]["properties"]
    assert batch_schema["capture_ids"]["maxItems"] == 8
    assert batch_schema["capture_ids"]["items"]["type"] == "string"
    assert [t["function"]["name"] for t in requests[2]["tools"]] == ["final_result"]
    returns = [m for m in requests[1]["messages"] if m["role"] == "tool"]
    page = json.loads(returns[-1]["content"])
    assert page["remaining_model_requests"] == 2
    assert len(page["pages"]) == 2
    assert all(p["text"] == '"native evidence"' for p in page["pages"])
    first_prompt = next(m["content"] for m in requests[0]["messages"] if m["role"] == "user")
    capture_summary = json.loads(first_prompt)["captures"][0]
    assert capture_summary["native_index"]["index_only_not_read"]
    assert "json:/title" in json.dumps(capture_summary["native_index"])
    assert "native evidence" not in json.dumps(capture_summary["native_index"])
