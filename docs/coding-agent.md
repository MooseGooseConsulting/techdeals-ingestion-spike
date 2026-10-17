# TechDeals coding-agent extractor harness

Use the **existing OpenCode coding agent** (its shell, file-editing, test loop and native session transcript) rather than an RLM or a custom agent. This repo adds only fixture preparation and independent evaluation. Nothing writes to Neon.

## Setup

Install [OpenCode](https://opencode.ai/docs/) and authenticate OpenRouter once or use your existing Doppler-injected `OPENROUTER_API_KEY`. You need Python 3.12+, `uv`, and the existing TechDeals Python environment, **no GPU**.

```bash
# Starting from repo root with PR #3's dependencies installed
uv run python scripts/coding_extractor.py prepare \
  --case spd --workspace .runs/coding-spd-001

# OpenRouter's explicitly :free route only; no paid fallback.
doppler run --project ai-automation --config dev -- \
  uv run python scripts/coding_extractor.py run \
  --workspace .runs/coding-spd-001 \
  --model openrouter/nvidia/nemotron-3-super-120b-a12b:free \
  --rounds 3

# Review or independently rerun evaluations without a model call
uv run python scripts/coding_extractor.py evaluate \
  --workspace .runs/coding-spd-001
```

Then try `--case ebay` in a **new** workspace. Additional development cases: `govdeals`, `hls`, `disctech`. `holdout-c` is not available to the experiment.

Each round invokes `opencode --pure run --format json --dir WORKSPACE --model MODEL --agent build --auto PROMPT`. It resumes the **same session** using `--session` after evaluation failures. OpenCode handles inspecting files, writing code, shell execution, tests and repair. The local harness merely checks output and sends back specific failures. `--pure` avoids external plugins; an inline config pins both main and small model to the selected free route. Unrelated environment secrets (including database credentials) are removed from both the agent and final extractor environment. The generated extractor itself receives no model API credentials while being independently evaluated; this first spike tests whether a coding agent can generate **reusable** extraction code rather than building a recurring inference service.

## Contract for generated extractor

The agent reads `captures.json`, `TASK.md`, the original native `inputs/*` and the actual Pydantic `contract.schema.json`. It generates `extractor.py` implementing:

```bash
python extractor.py --manifest captures.json --output extraction.json
```

Your existing `Source → Extraction` Pydantic contract and parser's source-reference validator are applied **after** the OpenCode run. The independent oracle is never copied into the agent's workspace or disclosed in the task. The evaluator checks every capture against its original source bytes before grading. Development evaluation compares the extracted priced-offer multiset against the existing oracle. A blind mutation changes prices and adds a product in the SPD case, or changes a variant price in the eBay case, to catch trivial hardcoding. This does **not** prove generalization to arbitrary formats or all semantic correctness (e.g. included-versus-supported components); those require further tests.

## Debugging and artifacts

All of the following live in `.runs/coding-...` (gitignored):

- `agent-01/events.jsonl`: OpenCode's structured tool/model step events, with additional rounds if needed.
- `agent-01/stderr.log`: provider, CLI and harness errors.
- `opencode-session.json`: OpenCode's **sanitized** native session export (where the session ID was available).
- `prompt-01.txt` and `evaluation-01.json`: input task and independently determined defects, for each round.
- `extractor.py`, `test_extractor.py`, `extraction.json`: reusable code and its output.
- `captures.json` and `inputs/*`: untouched source bytes and acquisition metadata.
- `run-summary.json`: status and attempt history.

OpenCode's regular TUI can open the session using `opencode --session <id>` when available. No custom HTML trace viewer or telemetry service.

## Known boundaries

- **Live inference not yet run here.** Offline tests exercise execution/evaluation, mutation rejection, failure feedback and session continuation using a fake OpenCode CLI boundary, not a real free model.
- This is a coding-agent architecture experiment, not a production ingestion replacement. The generated script remains in its workspace until reviewed and incorporated.
- Price and offer identity are checked. Full provenance fidelity, supported-versus-installed hardware, and same-post item identity still need semantic validation and later storage design.
- Free quotas can expire; a `:free` ID is a price gate, **not** a guarantee of provider availability or zero incidental tool/service charges. OpenCode run with `--pure` and only this model, no paid fallback.
**Evaluation isolation note:** The task workspace omits the oracle, and the harness checks original source hashes. A local process with full shell access is not a security boundary against a deliberately adversarial agent; a separate container or VM is needed for strict holdout secrecy. The existing dev corpus is appropriate for the initial engineering spike.
