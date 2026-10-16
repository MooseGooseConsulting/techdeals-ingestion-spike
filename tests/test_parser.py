import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx2 as httpx
import pytest
from openai import AsyncOpenAI
from pydantic import ValidationError
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, RetryPromptPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from techdeals_ingestion.models import Entry, Price, Source, SourceDate, SourceRef
from techdeals_ingestion.parser import parse
from techdeals_ingestion.settings import ParserSettings, fingerprint


def source(input_id="capture-1", content=b'{"name":"RTX 4070","ask":"$500"}'):
    return Source(
        input_id=input_id,
        acquisition_key=f"capture:{input_id}",
        content=content,
        media_type="application/json",
        namespace="marketplace",
        retrieved_at=datetime(2026, 10, 16, tzinfo=UTC),
        url="https://example.test/listing/1",
        metadata={"route": "search"},
    )


def output_call(info, entries=None, issues=None):
    extraction = {
        "entries": entries or [],
        "issues": issues or [],
    }
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, extraction)])


@pytest.mark.asyncio
async def test_agent_uses_typed_extraction_schema_and_readable_native_source(tmp_path):
    seen = []

    def respond(messages, info):
        prompt = "\n".join(
            str(part.content)
            for message in messages
            for part in getattr(message, "parts", [])
            if getattr(part, "part_kind", None) == "user-prompt"
        )
        seen.append((messages, info, prompt))
        return output_call(
            info,
            [
                {
                    "source_refs": [{"input_id": "capture-1", "locator": "json:/name"}],
                    "title": "RTX 4070",
                    "prices": [
                        {
                            "amount": "500",
                            "currency": "USD",
                            "original": "$500",
                            "kind": "ask",
                            "basis": None,
                            "conditions": None,
                        }
                    ],
                }
            ],
        )

    result = await parse([source()], ParserSettings.load(), tmp_path, model=FunctionModel(respond))

    assert len(seen) == 1
    _messages, info, prompt = seen[0]
    assert (
        'NATIVE SOURCE CONTENT (application/json) BEGIN\n{"name":"RTX 4070","ask":"$500"}' in prompt
    )
    assert '\\"name\\"' not in prompt
    schema = info.output_tools[0].parameters_json_schema
    assert schema["properties"].keys() == {"entries", "issues"}
    entry_schema = schema["$defs"]["Entry"]
    assert "prices" in entry_schema["properties"]
    assert "source_refs" in entry_schema["properties"]
    assert result.entries[0].prices[0].amount == Decimal(500)
    assert result.entries[0].source_refs[0].input_id == "capture-1"


@pytest.mark.asyncio
async def test_same_run_correction_rejects_unknown_input_then_accepts_valid_reference(tmp_path):
    histories = []

    def respond(messages, info):
        histories.append(messages)
        input_id = "invented-id" if len(histories) == 1 else "capture-1"
        return output_call(
            info,
            [{"source_refs": [{"input_id": input_id}], "title": "RTX 4070"}],
        )

    result = await parse([source()], ParserSettings.load(), tmp_path, model=FunctionModel(respond))

    assert len(histories) == 2
    assert any(
        isinstance(part, RetryPromptPart)
        for message in histories[1]
        for part in getattr(message, "parts", [])
    )
    assert result.entries[0].title == "RTX 4070"
    trace = json.loads((tmp_path / "run-001.json").read_text())
    assert trace["status"] == "success"
    assert (
        sum(
            part.get("part_kind") == "retry-prompt"
            for message in trace["messages"]
            for part in message.get("parts", [])
        )
        == 1
    )


@pytest.mark.asyncio
async def test_exhausted_validation_preserves_valid_entries_and_marks_uncovered_inputs(tmp_path):
    calls = []

    def respond(messages, info):
        calls.append(messages)
        return output_call(
            info,
            [
                {
                    "source_refs": [{"input_id": "capture-1"}],
                    "title": "RTX 4060" if len(calls) == 1 else "RTX 4070",
                }
            ],
        )

    result = await parse(
        [source(), source("capture-2", b"<p>Truncated source")],
        ParserSettings.load(),
        tmp_path,
        model=FunctionModel(respond),
    )

    assert len(calls) == 2
    assert [entry.title for entry in result.entries] == ["RTX 4070"]
    assert [issue.input_id for issue in result.issues] == ["capture-1", "capture-2"]
    assert all("validation retries were exhausted" in issue.message for issue in result.issues)
    assert result.entries[0].source_refs[0].input_id == "capture-1"
    assert json.loads((tmp_path / "run-001.json").read_text())["status"] == "partial"


@pytest.mark.asyncio
async def test_source_refs_validate_json_pointer_html_css_and_exact_text(tmp_path):
    inputs = [
        source(content=b'{"a/b":{"~key":["selected"]}}'),
        source("capture-2", b"<div class='card'>Selected item</div>"),
    ]

    def respond(messages, info):
        return output_call(
            info,
            [
                {
                    "source_refs": [
                        {"input_id": "capture-1", "locator": "json:/a~1b/~0key/0"},
                        {"input_id": "capture-2", "locator": "html:div.card"},
                    ],
                    "title": "selected",
                }
            ],
            [{"input_id": "capture-2", "message": "Raw title text is preserved."}],
        )

    result = await parse(inputs, ParserSettings.load(), tmp_path, model=FunctionModel(respond))
    assert len(result.entries) == 1
    assert result.entries[0].source_refs[0].locator == "json:/a~1b/~0key/0"


def _openrouter_reply(request, arguments):
    body = json.loads(request.content)
    tool_name = body["tools"][0]["function"]["name"]
    return httpx.Response(
        200,
        json={
            "provider": "test-provider",
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1,
            "model": body["model"],
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
                                "function": {"name": tool_name, "arguments": json.dumps(arguments)},
                            }
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18},
        },
    )


@pytest.mark.asyncio
async def test_openrouter_sdk_request_settings_and_trace_redact_secret(tmp_path, monkeypatch):
    secret = "test-openrouter-secret"
    monkeypatch.setenv("OPENROUTER_API_KEY", secret)
    settings = ParserSettings.load()
    requests = []

    def handle(request):
        requests.append(request)
        return _openrouter_reply(
            request,
            {
                "entries": [{"source_refs": [{"input_id": "capture-1"}], "title": "RTX 4070"}],
                "issues": [],
            },
        )

    transport = httpx.MockTransport(handle)
    client = httpx.AsyncClient(transport=transport)
    openai_client = AsyncOpenAI(
        base_url=settings.base_url,
        api_key=secret,
        max_retries=0,
        http_client=client,
    )
    injected_model = OpenRouterModel(
        settings.model,
        provider=OpenRouterProvider(openai_client=openai_client),
    )
    result = await parse([source()], settings, tmp_path, model=injected_model)

    assert result.entries[0].title == "RTX 4070"
    assert len(requests) == 1
    body = json.loads(requests[0].content)
    assert body["model"] == settings.model
    assert body["max_tokens"] == settings.max_output_tokens
    assert body["reasoning"] == {"enabled": True}
    trace = (tmp_path / "run-001.json").read_text()
    assert secret not in trace
    assert "authorization" not in trace.lower()
    assert "RTX 4070" in trace
    await openai_client.close()


@pytest.mark.asyncio
async def test_provider_error_trace_keeps_only_safe_status(tmp_path, monkeypatch):
    secret = "test-openrouter-secret"
    monkeypatch.setenv("OPENROUTER_API_KEY", secret)
    settings = ParserSettings.load()
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(429, json={"error": {"message": secret + " private body"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    openai_client = AsyncOpenAI(
        base_url=settings.base_url,
        api_key=secret,
        max_retries=0,
        http_client=client,
    )
    model = OpenRouterModel(
        settings.model, provider=OpenRouterProvider(openai_client=openai_client)
    )

    with pytest.raises(ModelHTTPError):
        await parse([source()], settings, tmp_path, model=model)

    trace = json.loads((tmp_path / "run-001.json").read_text())
    assert trace["status"] == "failed"
    assert trace["http_status"] == 429
    assert trace["exception_type"] == "ModelHTTPError"
    assert secret not in json.dumps(trace)
    assert "private body" not in json.dumps(trace)
    assert len(requests) == 1
    await openai_client.close()


def test_settings_are_small_strict_and_fingerprint_stable(tmp_path):
    settings = ParserSettings.load()
    assert len(ParserSettings.model_fields) == 7
    assert fingerprint(settings) == fingerprint(ParserSettings.load())
    source_text = Path("config/parser.toml").read_text()
    for invalid in [
        source_text + "\nunknown_key = 1\n",
        source_text.replace("max_input_bytes = 524288", 'max_input_bytes = "524288"'),
        source_text.replace("correction_retries = 1", "correction_retries = 2"),
    ]:
        path = tmp_path / "parser.toml"
        path.write_text(invalid)
        with pytest.raises(ValidationError):
            ParserSettings.load(path)


def test_output_models_keep_prices_nested_and_fields_ordinary():
    item = Entry(
        source_refs=[SourceRef(input_id="capture-1")],
        prices=[Price(original="$20", kind="ask", amount="20")],
        source_dates=[SourceDate(original_field="published", value="2026-01-02")],
    )
    assert item.prices[0].amount == Decimal(20)
    assert item.source_dates[0].original_field == "published"
