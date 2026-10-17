"""Load complete native inputs and collection context without interpreting listings."""

from __future__ import annotations

import hashlib
import json
import mimetypes
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import Source


@dataclass(frozen=True)
class SourcePackage:
    package_id: str
    sources: list[Source]
    declared_hashes: dict[str, str]


def _datetime(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value)
    if parsed is not None and parsed.utcoffset() is None:
        raise ValueError("retrieved_at must include an explicit timezone")
    return parsed


def load_manifest(path: str | Path, package_id: str) -> SourcePackage:
    """Load a manifest package, accepting only checked-in native corpus files."""
    manifest_path = Path(path).resolve()
    manifest = json.loads(manifest_path.read_text())
    package_ids = manifest.get("packages", {}).get(package_id)
    if package_ids is None:
        raise ValueError(f"Unknown package: {package_id}")
    records = {item["id"]: item for item in manifest.get("captures", [])}
    native_root = (manifest_path.parent.parent / "corpus" / "native").resolve()
    sources, hashes = [], {}
    for input_id in package_ids:
        record = records.get(input_id)
        if record is None:
            raise ValueError(f"Manifest package references unknown input: {input_id}")
        native_path = (manifest_path.parent.parent / record["path"]).resolve()
        if not native_path.is_relative_to(native_root):
            raise ValueError(f"Manifest input is outside corpus/native: {input_id}")
        meta = dict(record.get("metadata") or {})
        meta.update(record.get("provenance") or {})
        sources.append(
            Source(
                input_id=input_id,
                acquisition_key=record["acquisition_key"],
                content=native_path.read_bytes(),
                media_type=record.get("media_type") or "application/octet-stream",
                namespace=record.get("source_namespace"),
                retrieved_at=_datetime(meta.get("retrieved_at")),
                url=record.get("url"),
                linked_group=record.get("linked_group"),
                metadata=meta,
            )
        )
        if record.get("sha256"):
            hashes[input_id] = record["sha256"]
    return SourcePackage(package_id, sources, hashes)


def load_files(
    paths: list[str | Path],
    *,
    namespace: str,
    url: str | None = None,
    retrieved_at: str | datetime | None = None,
    acquisition_key: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> SourcePackage:
    """Read arbitrary native files as exact bytes, using caller-supplied context."""
    if not paths:
        raise ValueError("Supply at least one --input file")
    if not namespace.strip():
        raise ValueError("--namespace must not be empty")
    retrieved = _datetime(retrieved_at)
    extra = dict(metadata or {})
    sources = []
    for path_value in paths:
        path = Path(path_value)
        if not path.is_file():
            raise ValueError(f"Input is not a file: {path}")
        content = path.read_bytes()
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if acquisition_key:
            # Path identity, not argument position, so the same files retry as one batch.
            receipt_key = (
                acquisition_key if len(paths) == 1 else f"{acquisition_key}:{path.resolve()}"
            )
        else:
            # Same bytes are the same capture. A distinct sighting needs an explicit receipt.
            receipt = retrieved.isoformat() if retrieved else hashlib.sha256(content).hexdigest()
            receipt_key = f"{namespace}:{path.resolve()}:{receipt}"
        id_digest = hashlib.sha256(f"{receipt_key}\0{path.resolve()}".encode()).hexdigest()[:12]
        input_id = f"{path.stem[:80]}-{id_digest}"
        source_metadata = extra.copy()
        source_metadata.setdefault("filename", path.name)
        sources.append(
            Source(
                input_id=input_id,
                acquisition_key=receipt_key,
                content=content,
                media_type=media_type,
                namespace=namespace,
                retrieved_at=retrieved,
                url=url,
                metadata=source_metadata,
            )
        )
    return SourcePackage(Path(paths[0]).stem if len(paths) == 1 else "files", sources, {})
