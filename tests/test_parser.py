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
from techdeals_ingestion.parser import _complete_price_match, _request_hook, parse, plan_batches
from techdeals_ingestion.settings import ParserSettings, fingerprint


def source(
    input_id="capture-1",
    content=b'{"name":"RTX 4070","ask":"$500"}',
    *,
    linked_group=None,
    namespace="marketplace",
):
    return Source(
        input_id=input_id,
        acquisition_key=f"capture:{input_id}",
        content=content,
        media_type="application/json",
        namespace=namespace,
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
    assert body["reasoning"] == (
        {"enabled": True, "max_tokens": settings.reasoning_max_tokens}
        if settings.reasoning_enabled
        else {"enabled": False}
    )
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
    assert Price(original="14.99", kind="ask", amount=None).amount == Decimal("14.99")
    with pytest.raises(ValidationError):
        Price(original="14.99", kind="ask", amount="15.00")

    assert Price(original="14.99", kind="ask", amount="14.99").amount == Decimal("14.99")
    assert Price(original="Call for price", kind="ask", amount=None).amount is None


def test_formatted_price_amount_must_match_original():
    with pytest.raises(ValidationError):
        Price(original="$500", kind="ask", amount="5000")
    assert Price(original="$500", kind="ask", amount="500").amount == Decimal(500)
    assert Price(original="$1,250.50", kind="ask", amount="1250.50").amount == Decimal("1250.50")
    for original, amount in (("1e3", "1000"), ("+14.99", "14.99"), (".99", "0.99")):
        assert Price(original=original, kind="ask", amount=None).amount == Decimal(amount)
        with pytest.raises(ValidationError):
            Price(original=original, kind="ask", amount="10")
        assert Price(original=original, kind="ask", amount=amount).amount == Decimal(amount)
    for original in ("€20", "£20", "¥20"):
        with pytest.raises(ValidationError):
            Price(original=original, kind="ask", amount="30")
        assert Price(original=original, kind="ask", amount="20").amount == Decimal(20)
    with pytest.raises(ValidationError, match="amount must match the original price"):
        Price(original="Sale price: $500", kind="ask", amount="5000")
    assert Price(original="Sale price: $500", kind="ask", amount="500").amount == Decimal(500)
    with pytest.raises(ValidationError, match="not checkable"):
        Price(original="Call for price", kind="ask", amount="10")
    assert Price(original="Call for price", kind="ask", amount=None).amount is None
    with pytest.raises(ValidationError, match="not checkable"):
        Price(original="$10 or $20", kind="ask", amount="10")
    assert Price(original="$10 or $20", kind="ask", amount=None).amount is None


def test_complete_price_match_handles_delimited_text():
    assert not _complete_price_match("$20", "price: $200")
    assert not _complete_price_match("250", "count: 1,250 items")
    assert not _complete_price_match("20", "price: $200")
    assert not _complete_price_match("39", "value: 39.99")
    assert _complete_price_match("39.99", "value: 39.99,1.0")
    assert _complete_price_match("1,250.50", "total: 21,250.50 paid") is False
    assert _complete_price_match("250", "total: 1,250 paid") is False
    assert _complete_price_match("2026", "row: 33,2026 refreshed")
    assert _complete_price_match("2026-07-31T10:05:33.729223+00:00", "log,2026-07-31T10:05:33.729223+00:00,eBay")


@pytest.mark.asyncio
async def test_price_consistency_error_uses_normal_sdk_correction(tmp_path):
    histories = []

    async def respond(messages, info):
        histories.append(messages)
        amount = "15.00" if len(histories) == 1 else "14.99"
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
        [source(content=b'{"name":"disk","ask":"14.99"}')],
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
    assert len(ParserSettings.model_fields) == 9
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
                        "prices": [price | {"source_refs": [{"input_id": "capture-2"}]}],
                    }
                ],
            )

    result = await parse(
        [
            source(linked_group="listing"),
            source("capture-2", b'{"name":"other","ask":"$20"}', linked_group="listing"),
        ],
        ParserSettings.load(),
        tmp_path,
        model=FunctionModel(stream_function=respond),
    )

    assert len(histories) == 2
    assert result.entries[0].prices[0].source_refs[0].input_id == "capture-2"


@pytest.mark.asyncio
async def test_price_citation_must_appear_in_the_cited_capture(tmp_path):
    histories = []

    async def respond(messages, info):
        histories.append(messages)
        shared = {
            "source_refs": [{"input_id": "capture-1"}],
            "title": "RTX 4070",
        }
        if len(histories) == 1:
            yield output_call(
                info,
                [
                    shared
                    | {
                        "prices": [
                            {
                                "original": "$20",
                                "kind": "ask",
                                "amount": "20",
                                "source_refs": [{"input_id": "capture-1"}],
                            }
                        ]
                    }
                ],
            )
        else:
            yield output_call(
                info,
                [
                    shared
                    | {
                        "prices": [
                            {
                                "original": "$500",
                                "kind": "ask",
                                "amount": "500",
                                "source_refs": [{"input_id": "capture-1"}],
                            }
                        ]
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
    assert result.entries[0].prices[0].original == "$500"

    near = []

    async def near_match(messages, info):
        near.append(messages)
        original = "$20" if len(near) == 1 else "$200"
        amount = "20" if original == "$20" else "200"
        yield output_call(
            info,
            [
                {
                    "source_refs": [{"input_id": "capture-1"}],
                    "title": "near",
                    "prices": [
                        {
                            "original": original,
                            "kind": "ask",
                            "amount": amount,
                            "source_refs": [{"input_id": "capture-1"}],
                        }
                    ],
                }
            ],
        )

    near_result = await parse(
        [source(content=b'{"ask":"$200"}')],
        ParserSettings.load(),
        tmp_path / "near",
        model=FunctionModel(stream_function=near_match),
    )
    assert len(near) == 2
    assert near_result.entries[0].prices[0].original == "$200"

    misplaced = []

    async def wrong_listing(messages, info):
        misplaced.append(messages)
        original = "$20" if len(misplaced) == 1 else "$10"
        amount = "20" if original == "$20" else "10"
        yield output_call(
            info,
            [
                {
                    "source_refs": [{"input_id": "capture-1"}],
                    "title": "listing A",
                    "prices": [
                        {
                            "original": original,
                            "kind": "ask",
                            "amount": amount,
                            "source_refs": [
                                {"input_id": "capture-1", "locator": "json:/listing_a/ask"}
                            ],
                        }
                    ],
                }
            ],
        )

    located = await parse(
        [source(content=b'{"listing_a":{"ask":"$10"},"listing_b":{"ask":"$20"}}')],
        ParserSettings.load(),
        tmp_path / "located",
        model=FunctionModel(stream_function=wrong_listing),
    )
    assert len(misplaced) == 2
    assert located.entries[0].prices[0].original == "$10"

    fraction = []

    async def fractional_original(messages, info):
        fraction.append(messages)
        original = "99" if len(fraction) == 1 else "14.99"
        amount = "99" if original == "99" else "14.99"
        yield output_call(
            info,
            [
                {
                    "source_refs": [{"input_id": "capture-1"}],
                    "title": "fraction",
                    "prices": [{"original": original, "kind": "ask", "amount": amount}],
                }
            ],
        )

    fraction_result = await parse(
        [source(content=b'{"ask":"$14.99"}')],
        ParserSettings.load(),
        tmp_path / "fraction",
        model=FunctionModel(stream_function=fractional_original),
    )
    assert len(fraction) == 2
    assert fraction_result.entries[0].prices[0].original == "14.99"


@pytest.mark.asyncio
async def test_json_unicode_price_text_is_decoded_before_citation_check(tmp_path):
    histories = []

    async def respond(messages, info):
        histories.append(messages)
        original = "$200" if len(histories) == 1 else "$20"
        amount = "200" if original == "$200" else "20"
        yield output_call(
            info,
            [
                {
                    "source_refs": [{"input_id": "capture-1"}],
                    "title": "decoded",
                    "prices": [{"original": original, "kind": "ask", "amount": amount}],
                }
            ],
        )

    result = await parse(
        [source(content=b'{"ask":"\\u002420"}')],
        ParserSettings.load(),
        tmp_path,
        model=FunctionModel(stream_function=respond),
    )
    assert len(histories) == 2
    assert result.entries[0].prices[0].original == "$20"


@pytest.mark.asyncio
async def test_numeric_json_and_html_attribute_prices_are_cited(tmp_path):
    leaf = []

    async def numeric_leaf(messages, info):
        leaf.append(messages)
        original = "800" if len(leaf) == 1 else "799"
        yield output_call(
            info,
            [
                {
                    "source_refs": [{"input_id": "capture-1"}],
                    "title": "numeric leaf",
                    "prices": [
                        {
                            "original": original,
                            "kind": "ask",
                            "amount": original,
                            "source_refs": [
                                {"input_id": "capture-1", "locator": "json:/offers/price"}
                            ],
                        }
                    ],
                }
            ],
        )

    leaf_result = await parse(
        [source(content=b'{"offers":{"price":799}}')],
        ParserSettings.load(),
        tmp_path / "leaf",
        model=FunctionModel(stream_function=numeric_leaf),
    )
    assert len(leaf) == 2
    assert leaf_result.entries[0].prices[0].original == "799"

    parent = []

    async def numeric_parent(messages, info):
        parent.append(messages)
        original = "10" if len(parent) == 1 else "799"
        yield output_call(
            info,
            [
                {
                    "source_refs": [{"input_id": "capture-1"}],
                    "title": "numeric parent",
                    "prices": [
                        {
                            "original": original,
                            "kind": "ask",
                            "amount": original,
                            "source_refs": [{"input_id": "capture-1", "locator": "json:/offers"}],
                        }
                    ],
                }
            ],
        )

    parent_result = await parse(
        [source(content=b'{"offers":{"price":799}}')],
        ParserSettings.load(),
        tmp_path / "parent",
        model=FunctionModel(stream_function=numeric_parent),
    )
    assert len(parent) == 2
    assert parent_result.entries[0].prices[0].original == "799"

    attribute = []

    async def html_attribute(messages, info):
        attribute.append(messages)
        original = "499" if len(attribute) == 1 else "799"
        yield output_call(
            info,
            [
                {
                    "source_refs": [{"input_id": "capture-1"}],
                    "title": "attribute price",
                    "prices": [
                        {
                            "original": original,
                            "kind": "ask",
                            "amount": original,
                            "source_refs": [
                                {
                                    "input_id": "capture-1",
                                    "locator": "html:meta[itemprop=price]",
                                }
                            ],
                        }
                    ],
                }
            ],
        )

    html = b'<meta itemprop="price" content="799"><span data-rate="499">other</span>'
    attribute_result = await parse(
        [source(content=html)],
        ParserSettings.load(),
        tmp_path / "html",
        model=FunctionModel(stream_function=html_attribute),
    )
    assert len(attribute) == 2
    assert attribute_result.entries[0].prices[0].original == "799"

    quantity = []

    async def quantity_selector(messages, info):
        quantity.append(messages)
        if len(quantity) == 1:
            price = {
                "original": "1",
                "kind": "ask",
                "amount": "1",
                "source_refs": [{"input_id": "capture-1", "locator": "html:input[name=quantity]"}],
            }
        else:
            price = {
                "original": "$799.00",
                "kind": "ask",
                "amount": "799.00",
                "source_refs": [{"input_id": "capture-1", "locator": "html:span.product-price"}],
            }
        yield output_call(
            info,
            [
                {
                    "source_refs": [{"input_id": "capture-1"}],
                    "title": "DiscTech",
                    "prices": [price],
                }
            ],
        )

    disctech = (
        b'<input name="quantity" class="cart-quickaddtocart-quantity" type="number" value="1">'
        b'<span class="product-price" data-role="price-lead-formatted" data-rate="799">$799.00</span>'
        b'<meta content="799">'
    )
    quantity_result = await parse(
        [source(content=disctech)],
        ParserSettings.load(),
        tmp_path / "quantity",
        model=FunctionModel(stream_function=quantity_selector),
    )
    assert len(quantity) == 2
    assert quantity_result.entries[0].prices[0].original == "$799.00"


@pytest.mark.asyncio
async def test_json_numeric_evidence_keeps_the_source_spelling(tmp_path):
    histories = []
    lexical = "0.1234567890123456789"
    rounded = "0.12345678901234568"

    async def respond(messages, info):
        histories.append(messages)
        original = rounded if len(histories) == 1 else lexical
        yield output_call(
            info,
            [
                {
                    "source_refs": [{"input_id": "capture-1"}],
                    "title": "precise",
                    "prices": [
                        {
                            "original": original,
                            "kind": "ask",
                            "amount": original,
                            "source_refs": [{"input_id": "capture-1", "locator": "json:/ask"}],
                        }
                    ],
                }
            ],
        )

    result = await parse(
        [source(content=b'{"ask":0.1234567890123456789}')],
        ParserSettings.load(),
        tmp_path,
        model=FunctionModel(stream_function=respond),
    )
    assert len(histories) == 2
    assert result.entries[0].prices[0].original == lexical
    assert result.entries[0].prices[0].amount == Decimal(lexical)


@pytest.mark.asyncio
async def test_bom_prefixed_json_price_is_cited(tmp_path):
    histories = []

    async def respond(messages, info):
        histories.append(messages)
        original = "$200" if len(histories) == 1 else "$20"
        amount = "200" if original == "$200" else "20"
        yield output_call(
            info,
            [
                {
                    "source_refs": [{"input_id": "capture-1"}],
                    "title": "bom",
                    "prices": [
                        {
                            "original": original,
                            "kind": "ask",
                            "amount": amount,
                            "source_refs": [{"input_id": "capture-1", "locator": "json:/ask"}],
                        }
                    ],
                }
            ],
        )

    result = await parse(
        [source(content=b'\xef\xbb\xbf{"ask":"$20"}')],
        ParserSettings.load(),
        tmp_path,
        model=FunctionModel(stream_function=respond),
    )
    assert len(histories) == 2
    assert result.entries[0].prices[0].original == "$20"


@pytest.mark.asyncio
async def test_source_date_must_appear_in_the_cited_capture(tmp_path):
    histories = []

    async def respond(messages, info):
        histories.append(messages)
        if len(histories) == 1:
            source_date = {
                "original_field": "retrieved_at",
                "value": "2026-10-16T00:00:00+00:00",
            }
        else:
            source_date = {"original_field": "published", "value": "2026-01-02"}
        yield output_call(
            info,
            [
                {
                    "source_refs": [{"input_id": "capture-1"}],
                    "title": "dated",
                    "source_dates": [source_date],
                }
            ],
        )

    result = await parse(
        [source(content=b'{"name":"disk","published":"2026-01-02"}')],
        ParserSettings.load(),
        tmp_path,
        model=FunctionModel(stream_function=respond),
    )
    assert len(histories) == 2
    assert result.entries[0].source_dates[0].original_field == "published"
    assert result.entries[0].source_dates[0].value == "2026-01-02"


@pytest.mark.asyncio
async def test_locator_scoped_source_date_checks_value_not_field_name(tmp_path):
    histories = []

    async def respond(messages, info):
        histories.append(messages)
        yield output_call(
            info,
            [
                {
                    "source_refs": [{"input_id": "capture-1"}],
                    "title": "dated",
                    "source_dates": [
                        {
                            "original_field": "published_at",
                            "value": "2026-09-24T07:49:45-04:00",
                            "source_refs": [
                                {"input_id": "capture-1", "locator": "json:/published_at"}
                            ],
                        }
                    ],
                }
            ],
        )

    result = await parse(
        [source(content=b'{"published_at":"2026-09-24T07:49:45-04:00"}')],
        ParserSettings.load(),
        tmp_path,
        model=FunctionModel(stream_function=respond),
    )
    assert len(histories) == 1
    assert result.entries[0].source_dates[0].value == "2026-09-24T07:49:45-04:00"


def test_output_models_keep_prices_nested_and_fields_ordinary():
    item = Entry(
        source_refs=[SourceRef(input_id="capture-1")],
        prices=[Price(original="$20", kind="ask", amount="20")],
        source_dates=[SourceDate(original_field="published", value="2026-01-02")],
    )
    assert item.prices[0].amount == Decimal(20)
    assert item.source_dates[0].original_field == "published"


def test_plan_batches_respects_budget_and_preserves_order():
    a = source("a", b"x" * 60)
    b = source("b", b"y" * 60)
    c = source("c", b"z" * 60)
    batches = plan_batches([a, b, c], 100)
    assert [[s.input_id for s in batch] for batch in batches] == [["a"], ["b"], ["c"]]


def test_plan_batches_never_splits_a_linked_group():
    solo = source("solo", b"s" * 10)
    first = source("g1", b"g" * 60, linked_group="g")
    second = source("g2", b"g" * 60, linked_group="g")
    batches = plan_batches([solo, first, second], 100)
    assert [[s.input_id for s in batch] for batch in batches] == [["solo"], ["g1", "g2"]]


def test_plan_batches_collects_nonadjacent_linked_members():
    first = source("g1", b"g" * 10, linked_group="g")
    middle = source("solo", b"s" * 10)
    second = source("g2", b"g" * 10, linked_group="g")
    batches = plan_batches([first, middle, second], 1000)
    assert [[s.input_id for s in batch] for batch in batches] == [["g1", "g2"], ["solo"]]


def test_plan_batches_keeps_unrelated_namespaces_apart():
    a = source("a", b"x" * 10)
    b = source("b", b"y" * 10, namespace="other")
    batches = plan_batches([a, b], 1000)
    assert [[s.input_id for s in batch] for batch in batches] == [["a"], ["b"]]


def test_plan_batches_sends_an_oversize_single_input_alone():
    big = source("big", b"b" * 200)
    small = source("small", b"s" * 10)
    batches = plan_batches([big, small], 100)
    assert [[s.input_id for s in batch] for batch in batches] == [["big"], ["small"]]


def test_unambiguous_original_fills_a_missing_amount():
    assert Price(original="$280", kind="ask").amount == Decimal(280)
    assert Price(original="$20-$30", kind="ask").amount is None
    with pytest.raises(ValidationError):
        Price(original="$280", kind="ask", amount="281")


@pytest.mark.asyncio
async def test_null_amount_with_single_plain_original_is_filled_not_retried(tmp_path):
    histories = []

    async def respond(messages, info):
        histories.append(messages)
        yield output_call(
            info,
            [
                {
                    "source_refs": [{"input_id": "capture-1"}],
                    "title": "Disk",
                    "prices": [{"original": "$280", "kind": "ask"}],
                }
            ],
        )

    result = await parse(
        [source(content=b'{"item":"Disk","ask":"$280"}')],
        ParserSettings.load(),
        tmp_path,
        model=FunctionModel(stream_function=respond),
    )
    assert len(histories) == 1
    assert result.entries[0].prices[0].amount == Decimal(280)
