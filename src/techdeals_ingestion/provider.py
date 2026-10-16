"""One bounded PydanticAI/OpenRouter session shared by both parser candidates."""

import asyncio
import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Self

import httpx2 as httpx
from openai import AsyncOpenAI
from pydantic_ai import Agent, ToolOutput, capture_run_messages
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.usage import RunUsage, UsageLimits

from .settings import ParserSettings


class _CountToolAttempts(AbstractCapability):
    def __init__(self, session: "LLMRun") -> None:
        self.session = session

    async def before_model_request(self, ctx, request_context):
        # Byte-level BPE tokenizers cannot require more text tokens than UTF-8 bytes.
        # Serialize the complete request state, including all schemas and instructions.
        # JSON escaping and explicit framing slack deliberately overestimate input;
        # this preflight is not measured usage or an exact model tokenizer count.
        # Bound newly supplied user/tool content for this turn separately from history.
        # Previous exchanges remain governed by the full context check below.
        latest = request_context.messages[-1] if request_context.messages else None
        new_content_bytes = 0
        for part in getattr(latest, "parts", []):
            if isinstance(part, (UserPromptPart, ToolReturnPart)):
                content = part.content
                serialized = (
                    content if isinstance(content, str) else json.dumps(content, default=str)
                )
                new_content_bytes += len(serialized.encode("utf-8"))
        if new_content_bytes > self.session.settings.max_prepared_bytes:
            self.session.preflight_checks.append(
                {
                    "method": "new_turn_content_utf8_bytes",
                    "new_content_bytes": new_content_bytes,
                    "max_prepared_bytes": self.session.settings.max_prepared_bytes,
                    "accepted": False,
                }
            )
            raise UsageLimitExceeded("New turn content exceeds prepared source byte budget")
        messages = json.loads(ModelMessagesTypeAdapter.dump_json(request_context.messages))
        parameters = asdict(request_context.model_request_parameters)
        serialized_bytes = len(
            json.dumps(
                {
                    "messages": messages,
                    "parameters": parameters,
                    "settings": request_context.model_settings,
                },
                ensure_ascii=True,
                default=str,
            ).encode("utf-8")
        )
        framing_allowance = 4096 + 256 * (
            len(messages)
            + sum(len(message.get("parts", [])) for message in messages)
            + len(parameters.get("function_tools", []))
            + len(parameters.get("output_tools", []))
        )
        budget = {
            "method": "conservative_serialized_utf8_bytes_plus_framing",
            "serialized_bytes": serialized_bytes,
            "framing_allowance": framing_allowance,
            "reserved_output_tokens": self.session.settings.max_output_tokens,
            "context_window_tokens": self.session.settings.context_window_tokens,
        }
        budget["conservative_total"] = (
            serialized_bytes + framing_allowance + self.session.settings.max_output_tokens
        )
        budget["accepted"] = budget["conservative_total"] <= budget["context_window_tokens"]
        self.session.preflight_checks.append(budget)
        if not budget["accepted"]:
            raise UsageLimitExceeded("Conservative context preflight exceeds model context window")
        self.session.record_event(
            {
                "event": "model_request_preflight",
                "accepted": True,
                "completed_requests": self.session.usage.requests,
            }
        )
        return request_context

    async def after_model_request(self, ctx, *, request_context, response):
        self.session.record_event(
            {
                "event": "model_response",
                "tool_names": [p.tool_name for p in response.parts if isinstance(p, ToolCallPart)],
            }
        )
        # Count before schema validation/tool dispatch, including malformed arguments.
        self.session.tool_attempts += sum(isinstance(part, ToolCallPart) for part in response.parts)
        if self.session.tool_attempts > self.session.settings.max_tool_calls:
            self.session.usage.requests += 1
            self.session.blocked_responses.append(response)
            raise UsageLimitExceeded("Package tool call attempt limit exceeded")
        return response


class LLMRun:
    """Own cumulative package limits, SDK client, and inspectable request transcripts.

    ``run`` returns PydanticAI's AgentRunResult; callers use its ``output``.
    A session is sequential and must not be shared by concurrent packages.
    """

    def __init__(
        self,
        settings: ParserSettings,
        trace_dir: str | Path | None = None,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.trace_dir = Path(trace_dir) if trace_dir is not None else None
        self.usage = RunUsage()
        self.tool_attempts = 0
        self.preflight_checks: list[dict[str, Any]] = []
        self.blocked_responses: list = []
        self.started = time.monotonic()
        self.runs: list[dict[str, Any]] = []
        self.client = AsyncOpenAI(
            base_url=settings.base_url,
            api_key=os.environ["OPENROUTER_API_KEY"],
            max_retries=0,
            timeout=settings.timeout_seconds,
            http_client=http_client,
        )
        self.model = OpenRouterModel(
            settings.model,
            provider=OpenRouterProvider(openai_client=self.client),
        )
        self.limits = UsageLimits(
            request_limit=settings.max_requests,
            tool_calls_limit=settings.max_tool_calls,
        )

    def build_agent(
        self,
        output_type: Any,
        instructions: str,
        tools: Any = (),
        deps_type: Any = object,
        retry_budget: int | None = None,
    ) -> Agent:
        return Agent(
            self.model,
            output_type=ToolOutput(output_type),
            instructions=instructions,
            tools=tools,
            deps_type=deps_type,
            capabilities=[_CountToolAttempts(self)],
            retries=self.settings.correction_retries if retry_budget is None else retry_budget,
            model_settings=OpenRouterModelSettings(
                max_tokens=self.settings.max_output_tokens,
                openrouter_reasoning={"enabled": self.settings.reasoning_enabled},
            ),
        )

    async def run(self, agent: Agent, prompt: str, deps: Any = None) -> Any:
        record: dict[str, Any] = {"run": len(self.runs) + 1, "status": "running"}
        messages: list = []
        self.blocked_responses = []
        try:
            remaining = self.settings.package_timeout_seconds - (time.monotonic() - self.started)
            async with asyncio.timeout(max(0, remaining)):
                with capture_run_messages() as messages:
                    result = await agent.run(
                        prompt,
                        deps=deps,
                        usage=self.usage,
                        usage_limits=self.limits,
                    )
            record["status"] = "success"
            return result
        except BaseException as exc:
            # Exception text can contain provider bodies or credentials; retain only its type.
            record["status"] = "failed"
            record["exception_type"] = type(exc).__name__
            raise
        finally:
            record["messages"] = json.loads(
                ModelMessagesTypeAdapter.dump_json([*messages, *self.blocked_responses])
            )
            record["model_retry_prompts"] = sum(
                isinstance(part, RetryPromptPart) for message in messages for part in message.parts
            )
            record["usage"] = asdict(self.usage)
            record["elapsed_seconds"] = time.monotonic() - self.started
            self.runs.append(record)
            if self.trace_dir is not None:
                self.trace_dir.mkdir(parents=True, exist_ok=True)
                (self.trace_dir / f"run-{len(self.runs):03d}.json").write_text(
                    json.dumps({**self.stats(), "run": record}, indent=2, default=str) + "\n"
                )

    def record_event(self, event: dict[str, Any]) -> None:
        if self.trace_dir is not None:
            self.trace_dir.mkdir(parents=True, exist_ok=True)
            with (self.trace_dir / "events.jsonl").open("a") as output:
                output.write(
                    json.dumps({"elapsed_seconds": time.monotonic() - self.started, **event}) + "\n"
                )

    def stats(self) -> dict[str, Any]:
        return {
            "model": self.settings.model,
            "base_url": self.settings.base_url,
            "config_id": self.settings.config_id,
            "context_preflight": self.preflight_checks,
            "config": self.settings.model_dump(),
            "usage": asdict(self.usage),
            "tool_attempts": self.tool_attempts,
            "runs": len(self.runs),
            "elapsed_seconds": time.monotonic() - self.started,
            "failures": sum(run["status"] == "failed" for run in self.runs),
            "model_retry_prompts": sum(run["model_retry_prompts"] for run in self.runs),
        }

    async def aclose(self) -> None:
        await self.client.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
