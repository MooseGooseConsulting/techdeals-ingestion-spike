import json
from pathlib import Path

import httpx2 as httpx
import pytest
from pydantic import BaseModel, ValidationError
from pydantic_ai.exceptions import ModelHTTPError, UsageLimitExceeded

from techdeals_ingestion.provider import LLMRun
from techdeals_ingestion.settings import ParserSettings


class Answer(BaseModel):
    value: str


def reply(name, arguments):
    return httpx.Response(
        200,
        json={
            "provider": "test",
            "id": "test",
            "object": "chat.completion",
            "created": 1,
            "model": "nvidia/nemotron-3-super-120b-a12b:free",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": name,
                                    "arguments": json.dumps(arguments),
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
        },
    )


@pytest.mark.asyncio
async def test_sdk_tools_output_and_cumulative_limit(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-secret")
    settings = ParserSettings.load().model_copy(update={"max_requests": 3})
    requests = []

    def read_source() -> str:
        """Read captured evidence."""
        return "evidence"

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return reply("read_source", {})
        return reply("final_result", {"value": "evidence"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    async with LLMRun(settings, tmp_path, http_client=client) as session:
        agent = session.build_agent(Answer, "Extract.", tools=[read_source])
        assert (await session.run(agent, "source")).output.value == "evidence"
        assert (await session.run(agent, "source again")).output.value == "evidence"
        with pytest.raises(UsageLimitExceeded, match="request_limit"):
            await session.run(agent, "over budget")
        assert session.usage.requests == 3
        assert session.usage.input_tokens == 30
        assert session.usage.tool_calls == 1
        assert session.stats()["failures"] == 1
    assert len(requests) == 3
    assert requests[0]["model"] == settings.model
    assert requests[0]["max_tokens"] == settings.max_output_tokens
    names = {tool["function"]["name"] for tool in requests[0]["tools"]}
    assert names == {"read_source", "final_result"}
    assert any(msg["role"] == "tool" for msg in requests[1]["messages"])
    trace = json.loads((tmp_path / "run-003.json").read_text())
    assert trace["run"]["status"] == "failed"
    assert trace["run"]["exception_type"] == "UsageLimitExceeded"
    assert "test-secret" not in (tmp_path / "run-001.json").read_text()


@pytest.mark.asyncio
async def test_http_error_is_not_retried_and_is_traced(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-secret")
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    async with LLMRun(ParserSettings.load(), tmp_path, http_client=client) as session:
        with pytest.raises(ModelHTTPError):
            await session.run(session.build_agent(Answer, "Extract."), "source")
    assert len(calls) == 1
    trace = json.loads((tmp_path / "run-001.json").read_text())
    assert trace["run"]["status"] == "failed"
    assert trace["run"]["messages"]
    assert "authorization" not in json.dumps(trace).lower()


def test_settings_reject_unknown_keys_wrong_types_and_nonpositive_limits(tmp_path):
    raw = Path("config/parser.toml").read_text()
    for content in [
        raw + "\nunknown = 1\n",
        raw.replace("batch_size = 8", 'batch_size = "8"'),
        raw.replace("max_requests = 12", "max_requests = 0"),
    ]:
        path = tmp_path / "parser.toml"
        path.write_text(content)
        with pytest.raises(ValidationError):
            ParserSettings.load(path)


@pytest.mark.asyncio
async def test_malformed_tool_arguments_count_toward_package_attempt_limit(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-secret")
    settings = ParserSettings.load().model_copy(update={"max_tool_calls": 1})
    executed = []

    def read_source(index: int) -> str:
        """Read source by index."""
        executed.append(index)
        return "evidence"

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: reply("read_source", {"index": "invalid"}))
    )
    async with LLMRun(settings, http_client=client) as session:
        with pytest.raises(UsageLimitExceeded, match="attempt limit"):
            await session.run(
                session.build_agent(Answer, "Extract.", tools=[read_source]), "source"
            )
        assert session.tool_attempts == 2
        assert session.usage.requests == 2
    assert not executed
