"""Zero-service DSPy RLM debugging: record native LM history and REPL trajectory.

This module intentionally depends only on the Python standard library. It can
render saved reviews and run offline tests without installing DSPy or using an API.
"""

from __future__ import annotations

import html
import io
import json
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SENSITIVE_KEYS = frozenset({"api_key", "authorization", "proxy_authorization", "x-api-key"})


def _plain(value: Any) -> Any:
    """Convert DSPy/Pydantic objects into JSON-compatible information."""
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump(mode="json"))
    if hasattr(value, "toDict"):
        return _plain(value.toDict())
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _redact(value: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        return {
            k: "[REDACTED]" if k.lower() in SENSITIVE_KEYS else _redact(v, secrets)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value
    return value


def _save_json(path: Path, value: Any, secrets: tuple[str, ...]) -> None:
    value = _redact(_plain(value), secrets)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _history_text(lm: Any, secrets: tuple[str, ...]) -> str:
    history = getattr(lm, "history", None) or []
    if not history:
        return "No DSPy model-call history recorded.\n"
    stream = io.StringIO()
    try:
        lm.inspect_history(n=len(history), file=stream)
        result = stream.getvalue()
    except (AttributeError, TypeError, ValueError):
        result = json.dumps(_plain(history), indent=2, ensure_ascii=False)
    for secret in secrets:
        if secret:
            result = result.replace(secret, "[REDACTED]")
    return result


def _section(title: str, data: Any) -> str:
    if isinstance(data, str):
        body = data
    else:
        body = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    return (
        "<details open><summary>" + html.escape(title) + "</summary>"
        "<pre>" + html.escape(body) + "</pre></details>"
    )


def render_review(directory: str | Path) -> Path:
    """Render an HTML review from captured files; no DSPy/runtime/API required."""
    directory = Path(directory)
    meta = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    history = json.loads((directory / "model-history.json").read_text(encoding="utf-8"))
    trajectory_path = directory / "trajectory.json"
    trajectory = (
        json.loads(trajectory_path.read_text(encoding="utf-8"))
        if trajectory_path.exists() else []
    )
    result_path = directory / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else None
    sections = [_section("Run status and sources", meta)]
    sections.append("<h2>RLM execution trajectory</h2>")
    for index, step in enumerate(trajectory, 1):
        sections.append(_section(f"Iteration {index}: generated code and REPL output", step))
    if not trajectory:
        sections.append(
            "<p>No completed RLM trajectory was returned; inspect model calls below.</p>"
        )
    sections.append("<h2>Model calls: prompts and responses</h2>")
    for index, call in enumerate(history, 1):
        sections.append(_section(f"Model call {index}", call))
    if not history:
        sections.append("<p>No model calls recorded.</p>")
    transcript = directory / "prompts.txt"
    if transcript.exists():
        sections.append("<h2>DSPy formatted history</h2>")
        sections.append(
            _section("Full formatted transcript", transcript.read_text(encoding="utf-8"))
        )
    sections.append("<h2>Extraction output</h2>")
    sections.append(_section("Result", result or {"message": "No result returned"}))
    page = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>TechDeals RLM review</title><style>
:root{font-family:system-ui,sans-serif;color-scheme:dark}
body{max-width:1120px;margin:2rem auto;padding:0 1rem;background:#111827;color:#e5e7eb}
h1,h2{margin:1.4rem 0 .6rem}details{border:1px solid #374151;border-radius:.6rem;margin:.7rem 0;background:#1f2937}
summary{cursor:pointer;padding:.8rem 1rem;font-weight:650}
pre{white-space:pre-wrap;overflow-wrap:anywhere;padding:1rem;margin:0;border-top:1px solid #374151;font:13px/1.55 ui-monospace,monospace}
small{color:#9ca3af}a{color:#93c5fd}details:not([open]){background:#172033}
</style></head><body><h1>TechDeals · RLM execution review</h1>
<small>Static, offline review. Raw prompts, model responses and generated code are local to this run.</small>
""" + "\n".join(sections) + "</body></html>\n"
    output = directory / "review.html"
    output.write_text(page, encoding="utf-8")
    return output


def record_rlm_run(
    run: Callable[[], Any],
    lm: Any,
    output_directory: str | Path,
    *,
    metadata: dict[str, Any],
    secrets: tuple[str, ...] = (),
    validate: Callable[[Any], Any] | None = None,
) -> dict[str, Any]:
    """Run once, persist prompts and trajectory even on failed validation.

    `lm` is the DSPy LM used by both the main RLM and its sub-LM; thus a single
    history covers both. `run` performs the RLM invocation; `validate` is optional
    application-level validation of the returned `extraction` field.
    """
    destination = Path(output_directory)
    if destination.exists():
        raise FileExistsError(f"Review destination already exists: {destination}")
    destination.mkdir(parents=True)
    summary = {
        **metadata,
        "recorded_at": datetime.now(UTC).isoformat(),
        "status": "running",
    }
    prediction = None
    try:
        prediction = run()
        output = _plain(getattr(prediction, "extraction", None))
        if isinstance(output, str):
            output = json.loads(output)
        _save_json(destination / "result.json", output, secrets)
        summary["final_reasoning"] = getattr(prediction, "final_reasoning", "")
        # DSPy RLM invokes a separate extractor on exhausted max_iters. That is
        # not a successful explicit SUBMIT and must not be called a complete run.
        fallback = "Extract forced final output" in str(summary["final_reasoning"])
        summary["fallback"] = fallback
        if validate is not None:
            validate(output)
        summary["status"] = "partial" if fallback else "completed"
    except Exception as exc:  # noqa: BLE001 - capture failure artifacts without masking the run
        summary["status"] = "failed"
        summary["error_type"] = type(exc).__name__
        summary["error"] = str(exc)
        summary["traceback"] = traceback.format_exc()
    finally:
        if prediction is not None:
            _save_json(
                destination / "trajectory.json",
                getattr(prediction, "trajectory", []),
                secrets,
            )
        _save_json(destination / "model-history.json", getattr(lm, "history", []) or [], secrets)
        (destination / "prompts.txt").write_text(_history_text(lm, secrets), encoding="utf-8")
        summary["model_calls"] = len(getattr(lm, "history", []) or [])
        _save_json(destination / "run.json", summary, secrets)
        render_review(destination)
    return _redact(summary, secrets)
