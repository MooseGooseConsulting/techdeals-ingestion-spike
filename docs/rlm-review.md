# Minimal DSPy RLM debug harness (optional experiment)

No tracing server, browser extension, model download or GPU is required. The
existing `parser.py` and Neon ingestion path remain unchanged. This script
runs a separate **experimental** DSPy RLM on selected frozen captures and saves
DSPy's **native** prompt history and model-generated Python execution steps.

## Run one source through a free model

From the repository root, with your existing Doppler credentials:

```bash
PYDANTIC_AI_NO_BANNER=1 doppler run --project ai-automation --config dev -- \
  uv run --with 'dspy[deno]==3.4.1' python scripts/review_rlm.py run \
  --package dev-a --ids spd --out .runs/rlm-spd-001
```

To exercise linked eBay captures **with their manifest grouping retained**:

```bash
PYDANTIC_AI_NO_BANNER=1 doppler run --project ai-automation --config dev -- \
  uv run --with 'dspy[deno]==3.4.1' python scripts/review_rlm.py run \
  --package dev-a --ids archive-1 archive-8 archive-10 archive-16 \
  --out .runs/rlm-ebay-001
```

The runner defaults to the explicitly free OpenRouter model configured in the
current PR, `nvidia/nemotron-3-super-120b-a12b:free`; it rejects non-`:free`
model names and contains no paid-model fallback. Free-model provider quotas and
upstream rate limits still apply. The `--max-iters` and `--max-llm-calls` limits
bound the number of root iterations and submodel calls respectively; the default
is 8 and 12. The total model requests can exceed either limit individually.

The laptop runs only the Python/Deno sandbox: all model inference is remote.
The optional `dspy[deno]` extra provides a managed Deno executable for its
sandbox. The default project runtime is unchanged.

## Review an experiment

Open `.runs/rlm-spd-001/review.html` in any browser. It shows the exact saved
model prompts/responses and each returned RLM `{reasoning, code, output}` step.
There is no telemetry service or login. Raw records are also available:

| File | Contents |
|---|---|
| `model-history.json` | Native DSPy LM call history (including submodel calls through the same LM instance) |
| `prompts.txt` | DSPy's own formatted model-call history |
| `trajectory.json` | RLM code/reasoning/REPL outputs, when a prediction was returned |
| `result.json` | Returned extraction, even when application validation failed |
| `run.json` | Model, source hashes, limits, status, errors, fallback flag |
| `review.html` | Browser-readable static review assembled from the above |

To regenerate the HTML **entirely offline**, without DSPy or API credentials:

```bash
uv run python scripts/review_rlm.py review .runs/rlm-spd-001
```

This is intentionally an inspection **spike**, not an accepted TechDeals
parser or database ingestion. It validates the Pydantic extraction contract and
capture references, but it does not establish semantic completeness. Results
must not be written to Neon or treated as current prices. Holdout sources are
not selected by the example commands.

DSPy's `result.trajectory` is only returned for a completed call. If an RLM
crashes, the runner still saves calls already present in `lm.history` but cannot
reconstruct a missing trajectory. When DSPy's iteration-limit fallback creates
an answer instead of an explicit `SUBMIT`, the runner marks it `partial` rather
than a successful run. Free-model output quality and DSPy API compatibility must
be proven on live requests; offline tests only exercise artifact capture.
