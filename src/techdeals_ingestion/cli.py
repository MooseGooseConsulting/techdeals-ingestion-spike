"""Saved captures to validated, inspectable PostgreSQL interpretations."""

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
from pathlib import Path

from httpx2 import HTTPError
from openai import APIError
from psycopg import Error as DatabaseError

from .contracts import ParseBatchResult, UnitResult, prepare_units
from .evidence import EvidenceIndex, load_work_packages
from .probe import probe
from .provider import LLMRun
from .settings import ParserSettings
from .storage import VIEWS, Store


def _json(value):
    return json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n"


def _store(direct=False):
    key = "DATABASE_URL_UNPOOLED" if direct else "DATABASE_URL"
    dsn = os.environ.get(key)
    if not dsn:
        raise ValueError(f"Supply {key} through the process environment")
    return Store(dsn)


def trial_rows(store, attempt, experiment, arm):
    """Read accepted rows from this exact attempt, excluding older same-unit sightings."""
    with store.connect() as connection:
        rows = connection.execute(
            """SELECT s.* FROM listing_sightings s JOIN interpretations i USING(result_id)
            WHERE i.attempt_key=%s AND i.experiment_id=%s AND i.arm=%s""",
            (attempt, experiment, arm),
        ).fetchall()
        prices = connection.execute(
            "SELECT * FROM prices WHERE result_id=ANY(%s)",
            ([row["result_id"] for row in rows],),
        ).fetchall()
    return rows, prices


def parser_fingerprint(settings, arm, direct=False):
    folder = Path(__file__).parent
    names = [
        "contracts.py",
        "evidence.py",
        "validation.py",
        "provider.py",
        "settings.py",
        "instructions.py",
        "extraction.py",
        "cli.py",
        "staged.py" if arm == "staged" else "tool_parser.py",
    ]
    return hashlib.sha256(
        settings.config_id.encode()
        + str(direct).encode()
        + Path("uv.lock").read_bytes()
        + b"".join((folder / name).read_bytes() for name in names)
    ).hexdigest()


def _revision():
    value = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=False, capture_output=True, text=True
    )
    return value.stdout.strip() if value.returncode == 0 else None


async def run_package(args):
    packages = {p.package_id: p for p in load_work_packages(args.manifest)}
    if args.package not in packages:
        raise ValueError("Unknown package")
    package = packages[args.package]
    settings = ParserSettings.load(args.config)
    destination = Path(args.output)
    if destination.exists():
        raise ValueError("Use a new output directory; previous trials must remain inspectable")
    destination.mkdir(parents=True)
    store = _store()
    # Retention precedes EvidenceIndex: invalid native syntax must not lose its bytes.
    store.retain_package(package)
    fingerprint = parser_fingerprint(settings, args.arm, getattr(args, "direct", False))
    report = {
        "experiment": args.experiment,
        "arm": args.arm,
        "package": args.package,
        "source_revision": _revision(),
        "execution_strategy": "direct" if getattr(args, "direct", False) else args.arm,
        "parser_fingerprint": fingerprint,
        "config": settings.model_dump(),
        "reused": False,
    }
    try:
        index = EvidenceIndex(package.captures)
        source_bytes = sum(len(b) for b in index.originals.values())
        report["source_bytes"] = source_bytes
        if source_bytes > settings.max_package_bytes:
            raise ValueError("Package exceeds configured source allowance")
        result = (
            None
            if args.fresh
            else store.reuse(package, index, args.experiment, args.arm, fingerprint)
        )
        if result is not None:
            result.batch_id = package.package_id
            report["reused"] = True
            report["stats"] = {"model_requests": 0}
        else:
            if args.arm == "staged":
                from .staged import parse_batch
            else:
                from .tool_parser import parse_batch
            async with LLMRun(settings, destination / "trace") as session:
                try:
                    if getattr(args, "direct", False):
                        from .extraction import parse_direct

                        result = await parse_direct(package, index, session)
                    else:
                        result = await parse_batch(package, index, session)
                finally:
                    report["stats"] = session.stats()
                    report["coverage"] = getattr(session, "coverage", {})
        attempt = args.attempt or f"{args.experiment}:{args.arm}:{args.package}:{destination.name}"
        report["publication"] = store.publish(
            package,
            result,
            index,
            args.experiment,
            args.arm,
            attempt,
            parser_fingerprint=fingerprint,
        )
        report["units"] = [
            {"unit_id": u.unit_id, "status": u.status, "offers": len(u.offers), "issues": u.issues}
            for u in result.units
        ]
        (destination / "result.json").write_text(result.model_dump_json(indent=2) + "\n")
        rows, price_rows = trial_rows(store, attempt, args.experiment, args.arm)
        (destination / "database-rows.json").write_text(_json(rows))
        (destination / "database-prices.json").write_text(_json(price_rows))
        current = store.query("current_listings", experiment_id=args.experiment, arm=args.arm)
        (destination / "database-current.json").write_text(_json(current))
        report["database_rows"] = len(rows)
        report["status"] = (
            "completed_with_failures"
            if any(u.status == "failed" for u in result.units) or report["publication"]["errors"]
            else "completed"
        )
    except (ValueError, KeyError, OSError, RuntimeError, APIError, HTTPError, DatabaseError) as exc:
        report["status"] = "failed"
        report["exception_type"] = type(exc).__name__
        # Preparation failures still have an immutable failed attempt alongside retained evidence.
        if "index" in locals():
            failed = ParseBatchResult(
                batch_id=package.package_id,
                units=[
                    UnitResult(
                        unit_id=u.unit_id,
                        input_refs=u.input_refs,
                        status="failed",
                        issues=[f"Package stopped: {type(exc).__name__}"],
                    )
                    for u in prepare_units(package)
                ],
            )
            report["publication"] = store.publish(
                package,
                failed,
                index,
                args.experiment,
                args.arm,
                args.attempt
                or f"{args.experiment}:{args.arm}:{args.package}:{destination.name}:failed",
                parser_fingerprint=fingerprint,
            )
            (destination / "result.json").write_text(failed.model_dump_json(indent=2) + "\n")
        else:
            report["publication"] = store.record_failure(
                package,
                args.experiment,
                args.arm,
                args.attempt
                or f"{args.experiment}:{args.arm}:{args.package}:{destination.name}:failed",
                f"Preparation failed: {type(exc).__name__}",
            )
    (destination / "summary.json").write_text(_json(report))
    print(_json(report), end="")
    return 1 if report["status"] in ("failed", "completed_with_failures") else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect", help="Inspect selected native evidence without credentials")
    inspect.add_argument("--manifest", default="corpus/manifest.json")
    inspect.add_argument("--package", required=True)
    capability = sub.add_parser("probe", help="Run live model capability checks")
    capability.add_argument("--config", default="config/parser.toml")
    capability.add_argument("--output")
    sub.add_parser("migrate", help="Initialize the fresh isolated exercise database")
    run = sub.add_parser("run", help="Retain, parse and persist one frozen work package")
    run.add_argument("--manifest", default="corpus/manifest.json")
    run.add_argument("--config", default="config/parser.toml")
    run.add_argument("--package", required=True)
    run.add_argument("--arm", required=True, choices=("staged", "tool-reading"))
    run.add_argument("--experiment", required=True)
    run.add_argument("--output", required=True)
    run.add_argument("--attempt")
    run.add_argument(
        "--direct",
        action="store_true",
        help="One small typed batch; bypass discovery and inspection tools",
    )
    run.add_argument(
        "--fresh", action="store_true", help="Explicit new interpretation, bypass reuse"
    )
    evaluation = sub.add_parser(
        "evaluate", help="Inspect frozen semantic expectations and saved SQL reads"
    )
    evaluation.add_argument("--manifest", default="corpus/manifest.json")
    evaluation.add_argument("--oracle", default="corpus/oracle/expected.json")
    evaluation.add_argument("--package", required=True)
    evaluation.add_argument("--trial", required=True)
    evaluation.add_argument("--output", required=True)
    query = sub.add_parser("query", help="Read a documented SQL view")
    query.add_argument("view", choices=sorted(VIEWS))
    query.add_argument("--experiment")
    query.add_argument("--arm")
    args = parser.parse_args()
    if args.command == "inspect":
        package = next(p for p in load_work_packages(args.manifest) if p.package_id == args.package)
        index = EvidenceIndex(package.captures)
        print(
            _json(
                {
                    "package": package.package_id,
                    "source_bytes": sum(map(len, index.originals.values())),
                    "units": [u.model_dump() for u in prepare_units(package)],
                    "captures": [
                        {
                            "id": c.capture_id,
                            "bytes": len(index.originals[c.capture_id]),
                            "retrieved_at": c.retrieved_at,
                        }
                        for c in package.captures
                    ],
                }
            ),
            end="",
        )
    elif args.command == "probe":
        result = asyncio.run(probe(args.config, args.output))
        print(_json(result), end="")
        raise SystemExit(0 if all(r["passed"] for r in result.values()) else 1)
    elif args.command == "migrate":
        _store(direct=True).migrate()
        print("Initialized the configured exercise database")
    elif args.command == "evaluate":
        from .evaluate import evaluate, evaluate_persisted

        package = next(p for p in load_work_packages(args.manifest) if p.package_id == args.package)
        trial = Path(args.trial)
        result = ParseBatchResult.model_validate_json((trial / "result.json").read_text())
        report = evaluate(result, package, args.oracle)
        if (trial / "database-rows.json").exists():

            def read_saved(name):
                path = trial / name
                return json.loads(path.read_text()) if path.exists() else None

            report["persistence"] = evaluate_persisted(
                result,
                read_saved("database-rows.json"),
                read_saved("database-prices.json"),
                read_saved("database-current.json"),
            )
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise ValueError("Choose a new evaluation output; preserve earlier reviews")
        destination.write_text(_json(report))
        print(_json(report), end="")
    elif args.command == "query":
        print(_json(_store().query(args.view, experiment_id=args.experiment, arm=args.arm)), end="")
    else:
        raise SystemExit(asyncio.run(run_package(args)))


if __name__ == "__main__":
    main()
