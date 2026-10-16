"""Offline tests: no model access, no DSPy installation, no telemetry service."""

import json
from pathlib import Path
from types import SimpleNamespace

from techdeals_ingestion.rlm_review import record_rlm_run, render_review


class FakeLM:
    def __init__(self, history):
        self.history = history

    def inspect_history(self, n, file):
        file.write(f"Model calls: {n}\n")
        for call in self.history[-n:]:
            file.write(str(call) + "\n")


def test_complete_run_saves_reviewable_prompts_and_code(tmp_path):
    secret = "supersecret"
    lm = FakeLM([
        {
            "messages": [{"role": "user", "content": "Read 6 products"}],
             "response": {"message": "try Python"},
             "authorization": f"Bearer {secret}"},
        {"messages": [{"role": "user", "content": f"Do not echo {secret}"}],
         "response": "6 products"},
    ])
    prediction = SimpleNamespace(
        extraction={"entries": [], "issues": []},
        final_reasoning="SUBMIT invoked",
        trajectory=[{"reasoning": "Inspect", "code": "print(len(products))",
                     "output": "6"}],
    )
    output = tmp_path / "run1"
    summary = record_rlm_run(
        lambda: prediction, lm, output,
        metadata={"case": "spd"}, secrets=(secret,),
        validate=lambda result: assert_valid(result),
    )
    assert summary["status"] == "completed"
    assert summary["model_calls"] == 2
    assert json.loads((output / "trajectory.json").read_text())[0]["output"] == "6"
    assert "print(len(products))" in (output / "review.html").read_text()
    assert "Read 6 products" in (output / "prompts.txt").read_text()
    assert secret not in "".join(p.read_text() for p in output.iterdir())
    assert "[REDACTED]" in (output / "model-history.json").read_text()


def assert_valid(result):
    assert result["entries"] == []


def test_failed_run_retains_prompts_and_local_review(tmp_path):
    lm = FakeLM([{"messages": [{"role": "user", "content": "bad request"}]}])
    def crash():
        raise RuntimeError("Provider error")
    directory = tmp_path / "failed"
    summary = record_rlm_run(crash, lm, directory, metadata={"case": "ebay"})
    assert summary["status"] == "failed"
    assert "Provider error" in (directory / "run.json").read_text()
    assert "bad request" in (directory / "review.html").read_text()
    assert "No completed RLM trajectory" in (directory / "review.html").read_text()


def test_fallback_is_not_accepted_and_review_is_rerenderable(tmp_path):
    lm = FakeLM([])
    prediction = SimpleNamespace(extraction={"entries": [], "issues": []},
        final_reasoning="Extract forced final output", trajectory=[])
    directory = tmp_path / "fallback"
    summary = record_rlm_run(lambda: prediction, lm, directory, metadata={})
    assert summary["status"] == "partial"
    html_file = render_review(directory)
    assert html_file.name == "review.html"


def test_failed_validation_still_saves_result_and_trajectory(tmp_path):
    lm = FakeLM([])
    prediction = SimpleNamespace(extraction={"entries": []},
        final_reasoning="done", trajectory=[{"code": "x=1", "output": ""}])
    def reject(_):
        raise ValueError("No issues field")
    directory = tmp_path / "invalid"
    summary = record_rlm_run(lambda: prediction, lm, directory, metadata={}, validate=reject)
    assert summary["status"] == "failed"
    assert (directory / "result.json").exists()
    assert (directory / "trajectory.json").exists()
    assert "No issues field" in (directory / "review.html").read_text()
