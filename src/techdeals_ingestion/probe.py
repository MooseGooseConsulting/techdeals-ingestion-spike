"""Live typed-batch and evidence-tool check, separate from semantic acceptance."""

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from httpx2 import HTTPError
from openai import APIError
from pydantic import BaseModel, ConfigDict

from .provider import LLMRun
from .settings import ParserSettings


class ProbeItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    native_id: str
    title: str
    price: Decimal


class ProbeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ProbeItem]


async def probe(config="config/parser.toml", output=None, arms=("staged", "tool-reading")):
    settings = ParserSettings.load(config)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    folder = Path(output or f".runs/capability-{stamp}")
    if folder.exists():
        raise ValueError("Choose a new output directory to preserve the previous trial")
    folder.mkdir(parents=True)
    products = json.loads(Path("corpus/native/dev-a/spd.json").read_text())["products"][:2]
    material = json.dumps(products, ensure_ascii=False)
    expected = [(str(p["id"]), p["title"], Decimal(p["variants"][0]["price"])) for p in products]
    outcomes = {}
    for arm in arms:

        async def read_inputs(capture_id: str) -> str:
            """Read the native product records. The allowed capture_id is products."""
            if capture_id != "products":
                raise ValueError("Unknown capture")
            return material

        async with LLMRun(settings, folder / arm) as session:
            try:
                instructions = (
                    "Return exactly both native product IDs, titles and first variant prices."
                )
                if arm == "tool-reading":
                    instructions += (
                        ' First call read_inputs with capture_id="products" to read the evidence.'
                    )
                agent = session.build_agent(
                    ProbeResult, instructions, tools=[read_inputs] if arm == "tool-reading" else []
                )
                response = await session.run(
                    agent,
                    "Read products and return both items." if arm == "tool-reading" else material,
                )
                actual = [(i.native_id, i.title, i.price) for i in response.output.items]
                tool_read = any(
                    p.get("tool_name") == "read_inputs" and p.get("part_kind") == "tool-return"
                    for run in session.runs
                    for m in run["messages"]
                    for p in m.get("parts", [])
                )
                outcomes[arm] = {
                    "passed": sorted(actual) == sorted(expected) and (arm == "staged" or tool_read),
                    "output": response.output.model_dump(mode="json"),
                    "read_tool_called": tool_read,
                    "stats": session.stats(),
                }
            except (ValueError, OSError, RuntimeError, APIError, HTTPError) as exc:
                outcomes[arm] = {
                    "passed": False,
                    "exception_type": type(exc).__name__,
                    "stats": session.stats(),
                }
        (folder / "result.json").write_text(json.dumps(outcomes, indent=2, default=str) + "\n")
    return outcomes


if __name__ == "__main__":
    print(json.dumps(asyncio.run(probe()), indent=2, default=str))
