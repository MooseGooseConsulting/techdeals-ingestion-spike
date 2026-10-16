"""Typed extraction with one SDK conversation and its configured correction budget."""

import json

from pydantic_ai import ModelRetry

from .contracts import ParseBatchResult, UnitResult, prepare_units
from .instructions import SEMANTIC_INSTRUCTIONS
from .validation import validate_result


async def run_validated(session, agent, prompt, units, index, batch_id, deps=None):
    accepted = {}
    errors = []

    @agent.output_validator
    def validate(output: ParseBatchResult) -> ParseBatchResult:
        nonlocal errors
        if output.batch_id != batch_id:
            errors = ["batch_id must match supplied batch_id"]
            raise ModelRetry(errors[0])
        for expected in units:
            matches = [u for u in output.units if u.unit_id == expected.unit_id]
            candidate = ParseBatchResult(batch_id=batch_id, units=matches)
            if not validate_result(candidate, [expected], index):
                unit = matches[0]
                prior = accepted.get(expected.unit_id)
                if prior is not None:
                    if prior.status in ("complete", "no_offer"):
                        continue
                    if prior.status == "partial" and unit.status == "failed":
                        continue
                accepted[expected.unit_id] = unit.model_copy(deep=True)
        present = {unit.unit_id for unit in output.units}
        combined = output.model_copy(
            update={
                "units": [*output.units, *(u for key, u in accepted.items() if key not in present)]
            }
        )
        errors = [str(issue) for issue in validate_result(combined, units, index)]
        if errors:
            raise ModelRetry(
                "Correct invalid or missing units. Independently valid units are retained: "
                + json.dumps(list(accepted))
                + ". Validation errors: "
                + "; ".join(errors)
            )
        return combined

    try:
        await session.run(agent, prompt, deps=deps)
    except Exception as exc:  # noqa: BLE001 - isolate provider and exhausted-output failures
        errors = [*errors, f"Extraction stopped: {type(exc).__name__}"]
    return ParseBatchResult(
        batch_id=batch_id,
        units=[
            accepted.get(unit.unit_id)
            or UnitResult(
                unit_id=unit.unit_id,
                input_refs=unit.input_refs,
                status="failed",
                issues=errors or ["Invalid or missing model unit"],
            )
            for unit in units
        ],
    )


async def parse_direct(package, index, session):
    units = prepare_units(package)
    prompt = json.dumps(
        {
            "batch_id": package.package_id,
            "units": [
                {
                    **unit.model_dump(),
                    "captures": [
                        {
                            "capture_id": ref.capture_id,
                            "collection_context": index.captures[ref.capture_id].model_dump(
                                mode="json", exclude={"path"}
                            ),
                            "native": index.resolve(ref),
                        }
                        for ref in unit.input_refs
                    ],
                }
                for unit in units
            ],
        },
        ensure_ascii=False,
    )
    if len(prompt.encode()) > session.settings.max_prepared_bytes:
        raise ValueError("Direct batch exceeds the prepared input allowance")
    agent = session.build_agent(ParseBatchResult, SEMANTIC_INSTRUCTIONS)
    return await run_validated(session, agent, prompt, units, index, package.package_id)
