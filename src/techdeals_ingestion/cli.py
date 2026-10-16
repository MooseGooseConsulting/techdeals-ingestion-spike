"""Retain native source bytes, extract entries once, and inspect stored views."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .models import Extraction, SourceIssue
from .settings import ParserSettings
from .sources import SourcePackage, load_files, load_manifest
from .storage import VIEWS, Store


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n"


def _store() -> Store:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise ValueError("Supply DATABASE_URL through the process environment")
    return Store(dsn)


def _safe_source(source) -> dict[str, Any]:
    """Only print collection identifiers and dates, never arbitrary metadata."""
    return {
        "input_id": source.input_id,
        "bytes": len(source.content),
        "media_type": source.media_type,
        "namespace": source.namespace,
        "retrieved_at": source.retrieved_at,
        "url": source.url,
        "linked_group": source.linked_group,
    }


def _package(args) -> SourcePackage:
    if args.package:
        if args.input:
            raise ValueError("Use a corpus package or --input files, not both")
        return load_manifest(args.manifest, args.package)
    return load_files(
        args.input,
        namespace=args.namespace,
        url=args.url,
        retrieved_at=args.retrieved_at,
        acquisition_key=args.acquisition_key,
        metadata={"acquisition_metadata": args.metadata or {}},
    )


def _inspect(args) -> int:
    package = load_manifest(args.manifest, args.package)
    print(
        _json(
            {
                "package": package.package_id,
                "source_count": len(package.sources),
                "source_bytes": sum(len(s.content) for s in package.sources),
                "sources": [_safe_source(s) for s in package.sources],
            }
        ),
        end="",
    )
    return 0


def _snapshot(store: Store, view: str, attempt_id: str) -> list[dict[str, Any]]:
    return store.query(view, attempt_id=attempt_id)


async def _ingest(args) -> int:
    destination = Path(args.output)
    if destination.exists():
        raise ValueError("Choose a new output directory so each run remains inspectable")
    destination.mkdir(parents=True)
    trace_dir = destination / "trace"
    trace_dir.mkdir()
    package = _package(args)
    sources = package.sources
    store = _store()
    store.retain(sources)
    settings = ParserSettings.load(args.config)
    from . import parser

    parser_key = parser.fingerprint(settings)
    report: dict[str, Any] = {
        "package": package.package_id,
        "sources": [_safe_source(source) for source in sources],
        "parser_fingerprint": parser_key,
        "config": settings.model_dump(mode="json"),
        "status": "failed",
        "reused": False,
    }
    result: Extraction | None = None
    try:
        # Raw retention is committed before checksum checks, cache lookup or inference.
        for source in sources:
            declared = package.declared_hashes.get(source.input_id)
            if declared and hashlib.sha256(source.content).hexdigest() != declared:
                raise ValueError(f"Declared capture checksum mismatch: {source.input_id}")
        cached = None if args.fresh else store.cached(sources, parser_key)
        if cached is not None:
            result, origin_attempt = cached
            report["reused"] = True
        else:
            result = await parser.parse(sources, settings, trace_dir)
            origin_attempt = None
        covered = {ref.input_id for entry in result.entries for ref in entry.source_refs}
        reported = {issue.input_id for issue in result.issues}
        result.issues.extend(
            SourceIssue(
                input_id=source.input_id,
                message="No entry or source issue was returned for this input.",
            )
            for source in sources
            if source.input_id not in covered and source.input_id not in reported
        )
        saved = store.save(sources, result, parser_key, reuse_of=origin_attempt)
        report.update(saved)
        issues = [issue.model_dump(mode="json") for issue in result.issues]
        issues.extend(
            {"input_id": ref.input_id, "message": message}
            for entry in result.entries
            for message in entry.issues
            for ref in entry.source_refs[:1]
        )
        report["issues"] = issues
        report["status"] = (
            "completed_with_issues"
            if issues or (not result.entries and not result.issues)
            else "completed"
        )
        (destination / "result.json").write_text(_json(result))
        entries = _snapshot(store, "entries", saved["attempt_id"])
        prices = _snapshot(store, "prices", saved["attempt_id"])
        (destination / "database-entries.json").write_text(_json(entries))
        (destination / "database-prices.json").write_text(_json(prices))
        report["database_entries"] = len(entries)
        report["database_prices"] = len(prices)
    except Exception as exc:  # noqa: BLE001 - preserve failed attempts for provider and output errors
        error = {"type": type(exc).__name__}
        status_code = getattr(exc, "status_code", None)
        if isinstance(status_code, int):
            error["status_code"] = status_code
        report["error"] = error
        saved = store.save(sources, None, parser_key, error=json.dumps(error, sort_keys=True))
        report.update(saved)
        report["status"] = "failed"
        result = None
    if result is None:
        (destination / "result.json").write_text(_json({"status": "failed"}))
    (destination / "report.json").write_text(_json(report))
    print(_json(report), end="")
    return 1 if report["status"] != "completed" else 0


def build_parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="List native package inputs without secrets")
    inspect.add_argument("--manifest", default="corpus/manifest.json")
    inspect.add_argument("--package", required=True)
    commands.add_parser("migrate", help="Initialize the configured database")
    ingest = commands.add_parser("ingest", help="Retain and extract native files once")
    ingest.add_argument("--manifest", default="corpus/manifest.json")
    ingest.add_argument("--package", help="Existing corpus package")
    ingest.add_argument(
        "--input", action="append", default=[], help="Native input file (repeatable)"
    )
    ingest.add_argument("--namespace", default="local-import")
    ingest.add_argument("--url")
    ingest.add_argument("--retrieved-at")
    ingest.add_argument("--acquisition-key")
    ingest.add_argument("--metadata", action="append", metavar="KEY=VALUE")
    ingest.add_argument("--config", default="config/parser.toml")
    ingest.add_argument("--output", required=True)
    ingest.add_argument("--fresh", action="store_true", help="Skip unchanged-result reuse")
    query = commands.add_parser("query", help="Read a documented database view")
    query.add_argument("view", choices=sorted(VIEWS))
    query.add_argument("--attempt-id")
    return root


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "inspect":
        code = _inspect(args)
    elif args.command == "migrate":
        _store().migrate()
        print("Database migration complete")
        code = 0
    elif args.command == "ingest":
        if bool(args.package) == bool(args.input):
            raise SystemExit("ingest requires exactly one of --package or --input")
        metadata = {}
        for pair in args.metadata or []:
            if "=" not in pair:
                raise SystemExit("--metadata values must be KEY=VALUE")
            key, value = pair.split("=", 1)
            metadata[key] = value
        args.metadata = metadata
        code = asyncio.run(_ingest(args))
    else:
        rows = _store().query(
            args.view, **({"attempt_id": args.attempt_id} if args.attempt_id else {})
        )
        print(_json(rows), end="")
        code = 0
    raise SystemExit(code)


if __name__ == "__main__":
    main()
