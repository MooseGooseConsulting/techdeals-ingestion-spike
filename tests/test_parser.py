import asyncio
import json
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx2 as httpx
import pytest
from openai import AsyncOpenAI
from pydantic import ValidationError
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import RetryPromptPart
from pydantic_ai.models.function import DeltaThinkingPart, FunctionModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from techdeals_ingestion.models import Entry, Price, Source, SourceDate, SourceRef
from techdeals_ingestion.parser import _request_hook, parse
from techdeals_ingestion.settings import ParserSettings, fingerprint


def source(
    input_id="capture-1",
    content=b'{"name":"RTX 4070","ask":"$500"}',
    *,
    linked_group=None,
):
    return Source(
        input_id=input_id,
        acquisition_key=f"capture:{input_id}",
        content=content,
        media_type="application/json",
        namespace="marketplace",
        retrieved_at=datetime(2026, 10, 16, tzinfo=UTC),
        url="https://example.test/listing/1",
        linked_group=linked_group,
        metadata={"route": "search"},
    )


def output_call(info, entries=None, issues=None):
    return json.dumps(
        {
            "entries": entries or [],
            "issues": issues or [],
        }
    )


@pytest.mark.asyncio
async def test_agent_uses_typed_extraction_schema_and_readable_native_source(tmp_path):
    seen = []

    async def respond(messages, info):
        prompt = "\n".join(
            str(part.content)
            for message in messages
            for part in getattr(message, "parts", [])
            if getattr(part, "part_kind", None) == "user-prompt"
        )
        complete_prompt = "\n".join(
            [info.instructions or ""]
            + [
                str(part.content)
                for message in messages
                for part in getattr(message, "parts", [])
                if hasattr(part, "content")
            ]
        )
        seen.append((messages, info, prompt, complete_prompt))
        serialized = output_call(
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
        yield serialized[: len(serialized) // 2]
        yield serialized[len(serialized) // 2 :]

    result = await parse(
        [source()], ParserSettings.load(), tmp_path, model=FunctionModel(stream_function=respond)
    )

    assert len(seen) == 1
    _messages, info, prompt, complete_prompt = seen[0]
    assert (
        'NATIVE SOURCE CONTENT (application/json) BEGIN\n{"name":"RTX 4070","ask":"$500"}' in prompt
    )
    assert '\\"name\\"' not in prompt
    assert info.model_request_parameters.output_mode == "native"
    schema = info.model_request_parameters.output_object.json_schema
    assert schema["properties"].keys() == {"entries", "issues"}
    entry_schema = schema["$defs"]["Entry"]
    assert "prices" in entry_schema["properties"]
    assert "source_refs" in entry_schema["properties"]
    assert "Return data matching this JSON schema" in complete_prompt
    assert '"prices"' in complete_prompt
    assert '"title"' in complete_prompt
    assert result.entries[0].prices[0].amount == Decimal(500)
    assert result.entries[0].source_refs[0].input_id == "capture-1"
    assert result.entries[0].seller is None
    trace = json.loads((tmp_path / "run-001.json").read_text())
    assert trace["usage"]["requests"] == 1
    assert trace["usage"]["input_tokens"] > 0
    assert trace["usage"]["output_tokens"] > 0
    assert "cost" in trace["usage"]
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert events[-1]["event"]["event_kind"] == "agent_run_result"
    assert events[-1]["event"]["usage"]["requests"] == 1


@pytest.mark.asyncio
async def test_same_run_correction_rejects_unknown_input_then_accepts_valid_reference(tmp_path):
    histories = []

    async def respond(messages, info):
        histories.append(messages)
        input_id = "invented-id" if len(histories) == 1 else "capture-1"
        yield output_call(
            info,
            [{"source_refs": [{"input_id": input_id}], "title": "RTX 4070"}],
        )

    result = await parse(
        [source()], ParserSettings.load(), tmp_path, model=FunctionModel(stream_function=respond)
    )

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

    async def respond(messages, info):
        calls.append(messages)
        yield output_call(
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
        model=FunctionModel(stream_function=respond),
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
        source(content=b'{"a/b":{"~key":["selected"]}}', linked_group="listing"),
        source("capture-2", b"<div class='card'>Selected item</div>", linked_group="listing"),
    ]

    async def respond(messages, info):
        yield output_call(
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

    result = await parse(
        inputs, ParserSettings.load(), tmp_path, model=FunctionModel(stream_function=respond)
    )
    assert len(result.entries) == 1
    assert result.entries[0].source_refs[0].locator == "json:/a~1b/~0key/0"


def _openrouter_reply(request, arguments):
    body = json.loads(request.content)
    assert body["response_format"]["type"] == "json_schema"
    response_text = json.dumps(arguments)
    first_chunk = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": body["model"],
        "provider": "test-provider",
        "choices": [
            {
                "index": 0,
                "delta": {"content": response_text[: len(response_text) // 2]},
                "finish_reason": None,
            }
        ],
    }
    last_chunk = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": body["model"],
        "provider": "test-provider",
        "choices": [
            {
                "index": 0,
                "delta": {"content": response_text[len(response_text) // 2 :]},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 6,
            "total_tokens": 18,
            "cost": 0.001,
        },
    }
    stream = (
        "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in (first_chunk, last_chunk))
        + "data: [DONE]\n\n"
    )
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=stream,
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
    client = httpx.AsyncClient(
        transport=transport,
        event_hooks={"request": [_request_hook(tmp_path, time.monotonic(), secret)]},
    )
    openai_client = AsyncOpenAI(
        base_url=settings.base_url,
        api_key=secret,
        max_retries=0,
        http_client=client,
    )
    injected_model = OpenRouterModel(
        settings.model,
        provider=OpenRouterProvider(openai_client=openai_client),
        profile={"supports_json_schema_output": True},
    )
    result = await parse([source()], settings, tmp_path, model=injected_model)

    assert result.entries[0].title == "RTX 4070"
    assert len(requests) == 1
    body = json.loads(requests[0].content)
    assert body["model"] == settings.model
    assert body["max_tokens"] == settings.max_output_tokens
    assert body["reasoning"] == {"enabled": True}
    request_trace = json.loads((tmp_path / "request-001.json").read_text())
    assert request_trace["body"]["response_format"]["type"] == "json_schema"
    request_messages = request_trace["body"]["messages"]
    assert any(
        "Return data matching this JSON schema" in str(message.get("content", ""))
        for message in request_messages
    )
    assert any('"prices"' in str(message.get("content", "")) for message in request_messages)
    assert secret not in json.dumps(request_trace)
    assert "authorization" not in json.dumps(request_trace).lower()
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

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handle),
        event_hooks={"request": [_request_hook(tmp_path, time.monotonic(), secret)]},
    )
    openai_client = AsyncOpenAI(
        base_url=settings.base_url,
        api_key=secret,
        max_retries=0,
        http_client=client,
    )
    model = OpenRouterModel(
        settings.model,
        provider=OpenRouterProvider(openai_client=openai_client),
        profile={"supports_json_schema_output": True},
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
    assert (tmp_path / "request-001.json").exists()
    await openai_client.close()


@pytest.mark.asyncio
async def test_streamed_thinking_is_flushed_before_timeout_and_secret_is_redacted(
    tmp_path, monkeypatch
):
    secret = "stream-test-secret"
    monkeypatch.setenv("OPENROUTER_API_KEY", secret)

    async def thinking_then_hang(messages, info):
        yield {0: DeltaThinkingPart(content=f"source reasoning {secret}")}
        await asyncio.sleep(2)

    settings = ParserSettings.load().model_copy(update={"timeout_seconds": 1})
    with pytest.raises(TimeoutError):
        await parse(
            [source()],
            settings,
            tmp_path,
            model=FunctionModel(stream_function=thinking_then_hang),
        )

    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert events
    assert any("source reasoning" in json.dumps(event) for event in events)
    assert secret not in json.dumps(events)
    trace = json.loads((tmp_path / "run-001.json").read_text())
    assert trace["exception_type"] == "TimeoutError"
    assert secret not in json.dumps(trace)


@pytest.mark.parametrize("currency", ["None", "null", "unknown", "US", "USDollars"])
def test_currency_sentinel_and_malformed_codes_are_rejected(currency):
    with pytest.raises(ValidationError):
        Price(original="$20", kind="ask", amount="20", currency=currency)


def test_price_numeric_original_requires_matching_amount():
    with pytest.raises(ValidationError):
        Price(original="14.99", kind="ask", amount=None)
    with pytest.raises(ValidationError):
        Price(original="14.99", kind="ask", amount="15.00")

    assert Price(original="14.99", kind="ask", amount="14.99").amount == Decimal("14.99")
    assert Price(original="Call for price", kind="ask", amount=None).amount is None


@pytest.mark.asyncio
async def test_price_consistency_error_uses_normal_sdk_correction(tmp_path):
    histories = []

    async def respond(messages, info):
        histories.append(messages)
        amount = None if len(histories) == 1 else "14.99"
        yield output_call(
            info,
            [
                {
                    "source_refs": [{"input_id": "capture-1"}],
                    "prices": [{"original": "14.99", "kind": "ask", "amount": amount}],
                }
            ],
        )

    result = await parse(
        [source()],
        ParserSettings.load(),
        tmp_path,
        model=FunctionModel(stream_function=respond),
    )

    assert len(histories) == 2
    assert result.entries[0].prices[0].amount == Decimal("14.99")
    assert any(
        isinstance(part, RetryPromptPart)
        for message in histories[1]
        for part in getattr(message, "parts", [])
    )


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


@pytest.mark.asyncio
async def test_unlinked_inputs_cannot_share_an_entry(tmp_path):
    histories = []

    async def respond(messages, info):
        histories.append(messages)
        if len(histories) == 1:
            yield output_call(
                info,
                [
                    {
                        "source_refs": [
                            {"input_id": "capture-1"},
                            {"input_id": "capture-2"},
                        ],
                        "title": "merged",
                    }
                ],
            )
        else:
            yield output_call(
                info,
                [
                    {"source_refs": [{"input_id": "capture-1"}], "title": "one"},
                    {"source_refs": [{"input_id": "capture-2"}], "title": "two"},
                ],
            )

    result = await parse(
        [source(), source("capture-2", b'{"name":"other"}')],
        ParserSettings.load(),
        tmp_path,
        model=FunctionModel(stream_function=respond),
    )

    assert len(histories) == 2
    assert [entry.title for entry in result.entries] == ["one", "two"]


@pytest.mark.asyncio
async def test_multi_input_price_requires_its_own_source_ref(tmp_path):
    histories = []

    async def respond(messages, info):
        histories.append(messages)
        price = {"original": "$20", "kind": "ask", "amount": "20"}
        if len(histories) == 1:
            yield output_call(
                info,
                [
                    {
                        "source_refs": [
                            {"input_id": "capture-1"},
                            {"input_id": "capture-2"},
                        ],
                        "title": "bundle",
                        "prices": [price],
                    }
                ],
            )
        else:
            yield output_call(
                info,
                [
                    {
                        "source_refs": [
                            {"input_id": "capture-1"},
                            {"input_id": "capture-2"},
                        ],
                        "title": "bundle",
                        "prices": [price | {"source_refs": [{"input_id": "capture-1"}]}],
                    }
                ],
            )

    result = await parse(
        [
            source(linked_group="listing"),
            source("capture-2", b'{"name":"other"}', linked_group="listing"),
        ],
        ParserSettings.load(),
        tmp_path,
        model=FunctionModel(stream_function=respond),
    )

    assert len(histories) == 2
    assert result.entries[0].prices[0].source_refs[0].input_id == "capture-1"


def test_output_models_keep_prices_nested_and_fields_ordinary():
    item = Entry(
        source_refs=[SourceRef(input_id="capture-1")],
        prices=[Price(original="$20", kind="ask", amount="20")],
        source_dates=[SourceDate(original_field="published", value="2026-01-02")],
    )
    assert item.prices[0].amount == Decimal(20)
    assert item.source_dates[0].original_field == "published"
