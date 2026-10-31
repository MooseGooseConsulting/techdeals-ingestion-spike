"""Use OpenCode's real coding-agent loop to design and test reusable TechDeals extractors.

Only orchestrates isolated development fixtures and independent evaluation; it is
not an extraction engine or a replacement for TechDeals production persistence.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

CASES = {
    "spd": ("dev-a", ["spd"], "spd"),
    "ebay": ("dev-a", ["archive-1", "archive-8", "archive-10", "archive-16"], "ebay:397978307775"),
    "govdeals": ("dev-a", ["gov-asset", "gov-bid"], "govdeals:30467:94:1"),
    "hls": ("dev-a", ["hls"], "hls"),
    "disctech": ("dev-b", ["disctech"], "disctech"),
}

TASK = """# TechDeals: build a reusable extractor in this coding workspace

You have full access to read, write and execute programs inside this workspace.
Your work product is Python code that extracts sale listings from previously
unseen structures, **not** a manually transcribed answer for these files.

Inputs:
- `captures.json` lists input IDs, raw capture paths, media types, source IDs,
  acquisition metadata, and linked response groups. Open the referenced files.
- `contract.schema.json` describes the required output structure.
- No source-specific parser exists. Inspect the data and discover its semantics.

Implement `extractor.py` with this CLI:

    python extractor.py --manifest captures.json --output extraction.json

Output JSON must follow `contract.schema.json`: `entries` and `issues`.
Use original capture `input_id` values in each `source_refs` list; use real
JSON-Pointer `json:/...` or CSS `html:...` locators when practical.
Do not manufacture DB IDs or trusted observation times. Keep exact native IDs,
amounts, currency only when established, price kinds, and purchase scope.
Distinguish variants, bundles, supported versus installed hardware, separate
items in the same post, buyer requests, and linked responses. Preserve conflicts.
Avoid counting duplicate representations as new offers.

Work as a normal coding agent: explore files, implement, RUN the extractor,
inspect extracted results, write and run `test_extractor.py`, repair failures.
Make the program generalizable to changed IDs, prices, record counts and
compatible sources. Do not hardcode identifiers, amounts or the expected counts.

You may use available Python packages and write test fixtures. The independently
held evaluation oracle is not provided. Never inspect files outside this
workspace or invoke production databases. This task does not require Git commits.
When finished, leave `extractor.py` and `extraction.json` in the workspace.
"""


def _save(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n")


def prepare(root: Path, case: str, dest: Path) -> dict:
    """Copy only permitted dev captures into a new workspace, preserving bytes."""
    if case not in CASES:
        raise ValueError(f"Unknown case: {case}")
    if dest.exists():
        raise FileExistsError(f"Workspace already exists: {dest}")
    root = root.resolve()
    package, ids, _ = CASES[case]
    registry = json.loads((root / "corpus/manifest.json").read_text())
    if not set(ids) <= set(registry["packages"][package]):
        raise ValueError("Case includes a capture outside its development package")
    records = {x["id"]: x for x in registry["captures"]}
    dest.mkdir(parents=True)
    (dest / "inputs").mkdir()
    selected = []
    for capture_id in ids:
        record = records[capture_id]
        source = (root / record["path"]).resolve()
        if not source.is_relative_to((root / "corpus/native").resolve()):
            raise ValueError("Unsafe manifest path")
        raw = source.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if record.get("sha256") and digest != record["sha256"]:
            raise ValueError(f"Fixture checksum mismatch: {capture_id}")
        target = dest / "inputs" / f"{capture_id}{source.suffix}"
        target.write_bytes(raw)
        selected.append(
            {
                "input_id": capture_id,
                "path": str(target.relative_to(dest)),
                "media_type": record.get("media_type", "application/octet-stream"),
                "namespace": record.get("source_namespace"),
                "linked_group": record.get("linked_group"),
                "acquisition_key": record.get("acquisition_key"),
                "url": record.get("url"),
                "retrieved_at": (record.get("metadata") or {}).get("retrieved_at"),
                "sha256": digest,
            }
        )
    _save(dest / "captures.json", {"case": case, "captures": selected})
    # This schema is the *existing* application contract, not an invented one.
    sys.path.insert(0, str(root / "src"))
    from techdeals_ingestion.models import Extraction

    _save(dest / "contract.schema.json", Extraction.model_json_schema())
    (dest / "TASK.md").write_text(TASK)
    _save(dest / "harness.json", {"case": case, "schema": "Extraction"})
    return {"workspace": str(dest), "case": case, "captures": ids}


def _price(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return format(Decimal(str(value)).normalize(), "f")
    except (InvalidOperation, ValueError):
        return str(value)


def offer_keys(entries: list[dict]) -> Counter:
    """Multiset, since a sale post can contain several offers with one post ID."""
    keys: Counter = Counter()
    for entry in entries:
        for price in entry.get("prices", []):
            if price.get("amount") is not None and price.get("kind") not in {
                "shipping",
                "previous_price",
                "buyer_budget",
                "all_in_estimate",
            }:
                keys[
                    (
                        str(entry.get("native_id")),
                        str(entry.get("variant_id")),
                        _price(price["amount"]),
                    )
                ] += 1
    return keys


def expected_keys(oracle: dict) -> Counter:
    out: Counter = Counter()
    for row in oracle.get("offers", []):
        key = (str(row.get("native_id")), str(row.get("variant_id")), _price(row.get("amount")))
        out[key] += 1
    return out


def compare(extraction: dict, oracle: dict, case: str) -> dict:
    actual = offer_keys(extraction.get("entries", []))
    expected = expected_keys(oracle)
    missing = expected - actual
    extra = actual - expected
    errors = []
    if missing:
        errors.append("missing offers: " + repr(list(missing.elements())))
    if extra:
        errors.append("unexpected offers: " + repr(list(extra.elements())))
    if case == "spd":
        for entry in extraction.get("entries", []):
            if any(p.get("currency") is not None for p in entry.get("prices", [])):
                errors.append("SPD source does not establish currency")
                break
    if case == "hls":
        for entry in extraction.get("entries", []):
            if entry.get("native_id") == "t3_wanted1" and any(
                p.get("amount") is not None for p in entry.get("prices", [])
            ):
                errors.append("buyer request published as a priced seller offer")
                break
    if case == "govdeals":
        for entry in extraction.get("entries", []):
            for p in entry.get("prices", []):
                if _price(p.get("amount")) == "8280":
                    errors.append("derived all-in estimate mispublished as a seller price")
    return {
        "passed": not errors,
        "expected_offers": sum(expected.values()),
        "actual_priced_offers": sum(actual.values()),
        "errors": errors,
        "missing": [list(x) for x in missing.elements()],
        "extra": [list(x) for x in extra.elements()],
    }


def _runtime_env(*, inference: bool = False) -> dict[str, str]:
    """Only pass ordinary runtime settings, never database or unrelated API keys."""
    exact = {
        "PATH",
        "HOME",
        "USER",
        "SHELL",
        "LANG",
        "LC_ALL",
        "TERM",
        "TMPDIR",
        "VIRTUAL_ENV",
        "PYTHONPATH",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
    }
    result = {k: v for k, v in os.environ.items() if k in exact or k.startswith("UV_")}
    if inference and os.environ.get("OPENROUTER_API_KEY"):
        result["OPENROUTER_API_KEY"] = os.environ["OPENROUTER_API_KEY"]
    return result


def _execute(workspace: Path, manifest: Path, output: Path, timeout: int) -> dict:
    script = workspace / "extractor.py"
    if not script.is_file():
        return {"returncode": 127, "stderr": "Agent did not create extractor.py"}
    cmd = [sys.executable, str(script), "--manifest", str(manifest), "--output", str(output)]
    try:
        p = subprocess.run(
            cmd,
            cwd=workspace,
            text=True,
            capture_output=True,
            timeout=timeout,
            env=_runtime_env(),
            check=False,
        )
        return {"returncode": p.returncode, "stdout": p.stdout[-6000:], "stderr": p.stderr[-6000:]}
    except subprocess.TimeoutExpired:
        return {"returncode": 124, "stderr": f"Extractor exceeded {timeout}s"}


def _mutation(workspace: Path, case: str, directory: Path) -> tuple[Path, dict] | None:
    """Blind metamorphic test; a hardcoded listing-id/price program must fail."""
    if case not in {"spd", "ebay"}:
        return None
    directory.mkdir(exist_ok=True)
    manifest = json.loads((workspace / "captures.json").read_text())
    spec = copy.deepcopy(manifest)
    expected = {}
    for c in spec["captures"]:
        raw = json.loads((workspace / c["path"]).read_text())
        if case == "spd":
            first = raw["products"][0]
            first["variants"][0]["price"] = "17.37"
            cloned = copy.deepcopy(first)
            cloned["id"] = 999999991223
            cloned["title"] = "Mutated additional NVMe adapter"
            cloned["variants"][0]["id"] = 999999991224
            cloned["variants"][0]["product_id"] = cloned["id"]
            cloned["variants"][0]["price"] = "36.58"
            raw["products"].append(cloned)
            expected = {
                "changed": (str(first["id"]), str(first["variants"][0]["id"]), "17.37"),
                "added": (str(cloned["id"]), str(cloned["variants"][0]["id"]), "36.58"),
            }
        elif case == "ebay":
            for group in raw.get("variants", []):
                for option in group.get("variant_options", []):
                    if str(option["option_id"]) == "666281923652":
                        option["option_price"] = 6453
            if str(raw.get("variant_id")) == "666281923652":
                raw["price"] = "$6,453.00"
            expected = {"changed": (str(raw["product_id"]), "666281923652", "6453")}
        target = directory / Path(c["path"]).name
        target.write_text(json.dumps(raw, ensure_ascii=False))
        c["path"] = target.name
        c["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    new_manifest = directory / "captures.json"
    _save(new_manifest, spec)
    return new_manifest, expected


def _fixture_integrity(workspace: Path, root: Path, case: str) -> list[str]:
    """Verify against original native bytes, never agent-editable manifest hashes."""
    source_manifest = json.loads((root / "corpus/manifest.json").read_text())
    originals = {r["id"]: r for r in source_manifest["captures"]}
    work_manifest = json.loads((workspace / "captures.json").read_text())
    selected = work_manifest["captures"]
    expected_ids = CASES[case][1]
    if [r["input_id"] for r in selected] != expected_ids:
        return ["Agent changed the capture manifest identity or order"]
    issues = []
    for record in selected:
        capture_id = record["input_id"]
        original = originals[capture_id]
        raw = (root / original["path"]).read_bytes()
        copied = (workspace / record["path"]).resolve()
        if not copied.is_relative_to((workspace / "inputs").resolve()):
            issues.append(f"{capture_id}: capture path escaped inputs directory")
            continue
        expected_hash = hashlib.sha256(raw).hexdigest()
        if not copied.is_file() or hashlib.sha256(copied.read_bytes()).hexdigest() != expected_hash:
            issues.append(f"{capture_id}: source bytes changed from frozen corpus")
        if record.get("sha256") != expected_hash:
            issues.append(f"{capture_id}: capture metadata hash changed")
        for key, source_key in [
            ("namespace", "source_namespace"),
            ("linked_group", "linked_group"),
            ("media_type", "media_type"),
            ("acquisition_key", "acquisition_key"),
        ]:
            if record.get(key) != original.get(source_key):
                issues.append(f"{capture_id}: acquisition field {key} changed")
    return issues


def evaluate(
    workspace: Path,
    *,
    repo_root: Path | None = None,
    timeout: int = 90,
    mutation: bool = True,
) -> dict:
    """Run agent-written code against a withheld oracle; never inject it to the agent."""
    workspace = workspace.resolve()
    config = json.loads((workspace / "harness.json").read_text())
    root = (repo_root or Path(__file__).resolve().parents[1]).resolve()
    case = config["case"]
    if case not in CASES:
        return {"case": case, "passed": False, "errors": ["Unknown evaluation case"]}
    integrity = _fixture_integrity(workspace, root, case)
    if integrity:
        return {"case": case, "passed": False, "errors": integrity}
    oracle = json.loads((root / "corpus/oracle/expected.json").read_text())["cases"][CASES[case][2]]
    extracted = workspace / "extraction.json"
    extracted.unlink(missing_ok=True)
    exec_result = _execute(workspace, workspace / "captures.json", extracted, timeout)
    report: dict[str, Any] = {"case": case, "execution": exec_result, "passed": False}
    if exec_result["returncode"] != 0 or not extracted.exists():
        report["errors"] = ["Extractor failed or did not write extraction.json"]
        return report
    try:
        data = json.loads(extracted.read_text())
        sys.path.insert(0, str(root / "src"))
        from techdeals_ingestion.models import Extraction, Source
        from techdeals_ingestion.parser import _validate_output

        typed = Extraction.model_validate(data)
        fixture = json.loads((workspace / "captures.json").read_text())
        sources = [
            Source(
                input_id=s["input_id"],
                acquisition_key=s.get("acquisition_key") or s["input_id"],
                content=(workspace / s["path"]).read_bytes(),
                media_type=s["media_type"],
                namespace=s.get("namespace"),
                linked_group=s.get("linked_group"),
            )
            for s in fixture["captures"]
        ]
        errors = _validate_output(typed, sources)
        report["contract_errors"] = errors
        comparison = compare(typed.model_dump(mode="json"), oracle, case)
        report["comparison"] = comparison
        report["errors"] = errors + comparison["errors"]
        if mutation and not report["errors"]:
            with tempfile.TemporaryDirectory(prefix="td-mutated-") as temp:
                m = _mutation(workspace, case, Path(temp))
                if m:
                    test_manifest, expected = m
                    test_result = Path(temp) / "result.json"
                    mutation_exec = _execute(workspace, test_manifest, test_result, timeout)
                    report["mutation_execution"] = mutation_exec
                    if mutation_exec["returncode"] != 0 or not test_result.exists():
                        report["errors"].append("Extractor failed when source values changed")
                    else:
                        mutated = Extraction.model_validate_json(test_result.read_text())
                        observed = offer_keys(mutated.model_dump(mode="json")["entries"])
                        if not all(observed[k] == 1 for k in expected.values()):
                            report["errors"].append(
                                "Metamorphic test failed: updated prices or added product not extracted"
                            )
                    report["mutation_passed"] = not report["errors"]
        report["passed"] = not report["errors"]
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        report["errors"] = [f"Invalid extraction: {type(exc).__name__}: {exc}"]
    return report


def _session_id(events: Path) -> str | None:
    if not events.exists():
        return None
    for line in events.read_text(errors="replace").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        found = e.get("sessionID") or (e.get("data") or {}).get("sessionID")
        if isinstance(found, str) and found.startswith("ses_"):
            return found
    return None


def _agent_turn(
    workspace: Path,
    model: str,
    prompt: str,
    logs: Path,
    session: str | None,
    executable: str,
    timeout: int,
) -> tuple[int, str | None]:
    logs.mkdir(exist_ok=True)
    cmd = [
        executable,
        "--pure",
        "run",
        "--format",
        "json",
        "--dir",
        str(workspace),
        "--model",
        model,
        "--agent",
        "build",
        "--auto",
    ]
    if session:
        cmd += ["--session", session]
    cmd.append(prompt)
    env = _runtime_env(inference=True)
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(
        {
            "model": model,
            "small_model": model,
            "share": "disabled",
            "agent": {
                "build": {"model": model},
                "general": {"model": model},
                "explore": {"model": model},
                "scout": {"model": model},
            },
            "permission": {
                "edit": "allow",
                "bash": "allow",
                "read": "allow",
                "external_directory": "deny",
            },
        }
    )
    events = logs / "events.jsonl"
    err = logs / "stderr.log"
    with events.open("wb") as stdout, err.open("wb") as stderr:
        proc = subprocess.Popen(
            cmd,
            cwd=workspace,
            stdout=stdout,
            stderr=stderr,
            env=env,
            start_new_session=(os.name == "posix"),
        )
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
            proc.wait()
            code = 124
    return code, _session_id(events) or session


def run(
    workspace: Path,
    model: str,
    executable: str,
    *,
    repo_root: Path | None = None,
    rounds: int = 3,
    timeout: int = 900,
    extractor_timeout: int = 90,
) -> dict:
    workspace = workspace.resolve()
    if not model.endswith(":free"):
        raise ValueError("Refusing unverified paid model; explicitly select an :free model")
    if not (workspace / "captures.json").exists():
        raise ValueError("First prepare a workspace with the prepare command")
    if not shutil.which(executable):
        raise FileNotFoundError(f"Install OpenCode first; not found: {executable}")
    if model.startswith("openrouter/") and not os.environ.get("OPENROUTER_API_KEY"):
        raise ValueError("OPENROUTER_API_KEY must be available in process environment")
    session = None
    report = {}
    summary = {"model": model, "rounds": [], "session": None, "status": "running"}
    for round_no in range(1, rounds + 1):
        if round_no == 1:
            prompt = "Read TASK.md and captures.json. Implement and test the reusable extractor. Run it before finishing."
        else:
            prompt = (
                "Independent tests failed. Repair extractor.py, run your own tests, and rerun. "
                "Do not hardcode observed IDs or prices. Here is the evaluator feedback:\n"
                + json.dumps(report.get("errors", []), indent=2)
            )
        (workspace / f"prompt-{round_no:02d}.txt").write_text(prompt)
        code, session = _agent_turn(
            workspace,
            model,
            prompt,
            workspace / f"agent-{round_no:02d}",
            session,
            executable,
            timeout,
        )
        report = evaluate(workspace, repo_root=repo_root, timeout=extractor_timeout)
        _save(workspace / f"evaluation-{round_no:02d}.json", report)
        summary["rounds"].append(
            {
                "round": round_no,
                "agent_exit": code,
                "passed": report["passed"],
                "errors": report.get("errors", []),
            }
        )
        summary["session"] = session
        _save(workspace / "run-summary.json", summary)
        if code != 0 and not session:
            summary["status"] = "agent_error"
            break
        if report["passed"] and code == 0:
            summary["status"] = "passed"
            break
        if not session:
            summary["status"] = "missing_session_id"
            break
    else:
        summary["status"] = "failed"
    if session:
        try:
            export = subprocess.run(
                [executable, "export", "--sanitize", session],
                cwd=workspace,
                text=True,
                capture_output=True,
                timeout=40,
                env=_runtime_env(inference=True),
                check=False,
            )
            if export.returncode == 0:
                (workspace / "opencode-session.json").write_text(export.stdout)
            else:
                (workspace / "session-export-error.txt").write_text(export.stderr[-2000:])
        except (OSError, subprocess.TimeoutExpired) as exc:
            (workspace / "session-export-error.txt").write_text(str(exc))
    _save(workspace / "run-summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare")
    p.add_argument("--case", required=True, choices=sorted(CASES))
    p.add_argument("--workspace", type=Path, required=True)
    e = commands.add_parser("evaluate")
    e.add_argument("--workspace", type=Path, required=True)
    e.add_argument("--no-mutation", action="store_true")
    r = commands.add_parser("run")
    r.add_argument("--workspace", type=Path, required=True)
    r.add_argument("--model", default="openrouter/nvidia/nemotron-3-super-120b-a12b:free")
    r.add_argument("--opencode", default="opencode")
    r.add_argument("--rounds", type=int, default=3)
    r.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        print(json.dumps(prepare(args.repo_root, args.case, args.workspace), indent=2))
        return 0
    if args.command == "evaluate":
        report = evaluate(args.workspace, repo_root=args.repo_root, mutation=not args.no_mutation)
        print(json.dumps(report, indent=2))
        return 0 if report["passed"] else 1
    if args.rounds < 1 or args.timeout < 1:
        parser.error("rounds and timeout must be positive")
    summary = run(
        args.workspace,
        args.model,
        args.opencode,
        repo_root=args.repo_root,
        rounds=args.rounds,
        timeout=args.timeout,
    )
    print(json.dumps(summary, indent=2))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
