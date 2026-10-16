"""Extract five fields from one native product using PydanticAI directly.

Run with Doppler's ai-automation/dev environment. Each run keeps its output and
SDK conversation under .runs/basic-product/. This does not call the ingestion store.
"""

import asyncio
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from openai import APIConnectionError
from pydantic import BaseModel, Field
from pydantic_ai import Agent, NativeOutput, capture_run_messages
from pydantic_ai.exceptions import AgentRunError
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.models.openrouter import OpenRouterModel


class Product(BaseModel):
    title: str
    sku: str
    price: Decimal
    currency: str | None = Field(
        pattern=r"^[A-Z]{3}$",
        description="Currency code stated in the source, otherwise JSON null.",
    )
    published_at: datetime


async def main():
    destination = Path(".runs/basic-product") / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination.mkdir(parents=True)
    source = Path("corpus/native/small-a/small-spd.json").read_text()
    agent = Agent(
        OpenRouterModel(
            "nvidia/nemotron-3-super-120b-a12b:free",
            profile={"supports_json_schema_output": True},
        ),
        output_type=NativeOutput(Product, template="Extract into this JSON schema:\n{schema}"),
        instructions=(
            "Extract the product and its first variant price and SKU. "
            "Preserve the publication date and timezone. "
            'Missing currency must be JSON null, never the string "None" or "null".'
        ),
        retries=0,
        model_settings={
            "max_tokens": 2048,
            "timeout": 45,
            "openrouter_reasoning": {"enabled": True},
        },
    )
    with capture_run_messages() as messages:
        try:
            async with asyncio.timeout(60):
                result = await agent.run(source)
            rendered = result.output.model_dump_json(indent=2)
            (destination / "result.json").write_text(rendered + "\n")
            print(rendered)
            print(result.usage)
        except (AgentRunError, APIConnectionError, TimeoutError) as exc:
            print(f"Failed: {type(exc).__name__}; HTTP status: {getattr(exc, 'status_code', None)}")
            raise SystemExit(1) from None
        finally:
            trace = ModelMessagesTypeAdapter.dump_json(messages, indent=2).decode()
            secret = os.environ.get("OPENROUTER_API_KEY")
            if secret:
                trace = trace.replace(secret, "[REDACTED]")
            (destination / "messages.json").write_text(trace)
            print(f"Run artifacts: {destination}")


if __name__ == "__main__":
    asyncio.run(main())
