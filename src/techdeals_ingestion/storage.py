"""Small PostgreSQL store for retained inputs and extracted entries."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .models import Entry, Extraction, Source

VIEWS = frozenset({"entries", "prices", "current_entries", "current_prices", "history", "failures"})


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _source_context(source: Source) -> dict[str, Any]:
    """Context that can change interpretation; receipt IDs and trusted time are excluded."""
    metadata = {
        key: value
        for key, value in source.metadata.items()
        if key not in {"input_id", "acquisition_key", "retrieved_at", "sha256"}
    }
    return {
        "content_sha256": hashlib.sha256(source.content).hexdigest(),
        "media_type": source.media_type,
        "namespace": source.namespace,
        "url": source.url,
        "metadata": metadata,
    }


def _row_context(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "content_sha256": row["sha256"],
        "media_type": row["media_type"],
        "namespace": row["namespace"],
        "url": row["url"],
        "metadata": {
            key: value
            for key, value in row["metadata"].items()
            if key not in {"input_id", "acquisition_key", "retrieved_at", "sha256"}
        },
    }


def _link_signatures(items: list[dict[str, Any]]) -> dict[str, tuple[str, ...] | None]:
    """Group membership is the sorted content of the linked inputs, not their receipt IDs."""
    members: dict[str, list[str]] = {}
    for item in items:
        if item["linked_group"]:
            members.setdefault(item["linked_group"], []).append(item["context_digest"])
    signatures: dict[str, tuple[str, ...] | None] = {}
    for item in items:
        if item["linked_group"]:
            signatures[item["input_id"]] = tuple(sorted(members[item["linked_group"]]))
        else:
            signatures[item["input_id"]] = None
    return signatures


def _source_items(sources: list[Source]) -> list[dict[str, Any]]:
    items = []
    for source in sources:
        context = _source_context(source)
        items.append(
            {
                "input_id": source.input_id,
                "context": context,
                "context_digest": _digest(context),
                "linked_group": source.linked_group,
            }
        )
    return items


def _row_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = []
    for row in rows:
        context = _row_context(row)
        items.append(
            {
                "input_id": row["input_id"],
                "context": context,
                "context_digest": _digest(context),
                "linked_group": row["linked_group"],
            }
        )
    return items


def _rebind_mapping(
    old_items: list[dict[str, Any]], new_items: list[dict[str, Any]]
) -> dict[str, str] | None:
    """Pair cached inputs to new receipts by content and linked-group membership."""
    old_signatures = _link_signatures(old_items)
    new_signatures = _link_signatures(new_items)
    old_groups: dict[tuple[str, tuple[str, ...] | None], list[str]] = {}
    new_groups: dict[tuple[str, tuple[str, ...] | None], list[str]] = {}
    for item, signatures, groups in (
        (old_items, old_signatures, old_groups),
        (new_items, new_signatures, new_groups),
    ):
        for entry in item:
            key = (entry["context_digest"], signatures[entry["input_id"]])
            groups.setdefault(key, []).append(entry["input_id"])
    if set(old_groups) != set(new_groups):
        return None
    mapping: dict[str, str] = {}
    for key, old_ids in old_groups.items():
        new_ids = new_groups[key]
        if len(old_ids) != len(new_ids):
            return None
        mapping.update(zip(sorted(old_ids), sorted(new_ids), strict=True))
    if len(mapping) != len(old_items):
        return None
    return mapping


def _reusable(result: Extraction) -> bool:
    """Only complete, issue-free extractions may be replayed for later acquisitions."""
    if result.issues or any(entry.issues for entry in result.entries):
        return False
    return bool(result.entries)


def semantic_key(sources: list[Source], parser_key: str) -> str:
    """Stable exact-content key for parser reuse across later acquisition receipts."""
    contexts = [_source_context(source) for source in sources]
    # Preserve whether inputs were linked without making the collector's group ID semantic.
    groups: dict[str, list[str]] = {}
    for source, context in zip(sources, contexts, strict=True):
        if source.linked_group:
            groups.setdefault(source.linked_group, []).append(_digest(context))
    linked = sorted(sorted(values) for values in groups.values())
    return _digest(
        {
            "version": 1,
            "parser_key": parser_key,
            "sources": sorted(contexts, key=_json),
            "linked": linked,
        }
    )


def _typed_source_refs(extraction: Extraction, mapping: dict[str, str]) -> Extraction:
    """Rebind only contract source_refs. Identical-looking native JSON stays untouched."""
    data = extraction.model_dump(mode="python")

    def rebind(refs: list[dict[str, Any]]) -> None:
        for ref in refs:
            ref["input_id"] = mapping.get(ref["input_id"], ref["input_id"])

    for entry in data["entries"]:
        rebind(entry["source_refs"])
        for price in entry["prices"]:
            rebind(price["source_refs"])
        for source_date in entry["source_dates"]:
            rebind(source_date["source_refs"])
    for issue in data["issues"]:
        issue["input_id"] = mapping.get(issue["input_id"], issue["input_id"])
    return Extraction.model_validate(data)


def _meaning(entry: Entry) -> dict[str, Any]:
    """Hash listing facts without receipt-specific source references."""
    data = entry.model_dump(mode="json")
    data.pop("source_refs", None)
    for price in data.get("prices") or []:
        price.pop("source_refs", None)
    for source_date in data.get("source_dates") or []:
        source_date.pop("source_refs", None)
    return data


def _safe_error(error: str) -> str:
    """Keep useful failure context while removing common inline credential forms."""
    value = re.sub(r"(://[^:/\s]+:)[^@/\s]+@", r"\1[redacted]@", str(error))
    value = re.sub(
        r"(?i)\b(password|passwd|token|api[_-]?key|secret)\s*([=:])\s*[^\s,;]+",
        r"\1\2[redacted]",
        value,
    )
    return value[:2000]


class Store:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def connect(self):
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def migrate(self) -> None:
        sql = Path(__file__).with_name("schema.sql").read_text()
        with self.connect() as conn:
            conn.execute(sql)

    def retain(self, sources: list[Source]) -> None:
        """Persist exact bytes and acquisition receipts before parser work begins."""
        with self.connect() as conn:
            for source in sources:
                sha = hashlib.sha256(source.content).hexdigest()
                conn.execute(
                    "INSERT INTO ingest_raw_content(sha256,content) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                    (sha, source.content),
                )
                metadata = Jsonb(source.metadata)
                conn.execute(
                    """INSERT INTO ingest_sources
                    (input_id,acquisition_key,sha256,media_type,namespace,retrieved_at,url,linked_group,metadata)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING""",
                    (
                        source.input_id,
                        source.acquisition_key,
                        sha,
                        source.media_type,
                        source.namespace,
                        source.retrieved_at,
                        source.url,
                        source.linked_group,
                        metadata,
                    ),
                )
                existing = conn.execute(
                    "SELECT * FROM ingest_sources WHERE input_id=%s OR acquisition_key=%s",
                    (source.input_id, source.acquisition_key),
                ).fetchall()
                expected = {
                    "input_id": source.input_id,
                    "acquisition_key": source.acquisition_key,
                    "sha256": sha,
                    "media_type": source.media_type,
                    "namespace": source.namespace,
                    "retrieved_at": source.retrieved_at,
                    "url": source.url,
                    "linked_group": source.linked_group,
                    "metadata": source.metadata,
                }
                if len(existing) != 1 or existing[0] != expected:
                    raise ValueError(
                        "input or acquisition key already retained with different source data"
                    )

    def _retained(self, conn, sources: list[Source]) -> dict[str, dict[str, Any]]:
        ids = [source.input_id for source in sources]
        rows = conn.execute(
            "SELECT * FROM ingest_sources WHERE input_id = ANY(%s)", (ids,)
        ).fetchall()
        by_id = {row["input_id"]: row for row in rows}
        for source in sources:
            row = by_id.get(source.input_id)
            if row is None or row["acquisition_key"] != source.acquisition_key:
                raise ValueError(f"source must be retained before parsing: {source.input_id}")
            expected = {
                "sha256": hashlib.sha256(source.content).hexdigest(),
                "media_type": source.media_type,
                "namespace": source.namespace,
                "retrieved_at": source.retrieved_at,
                "url": source.url,
                "linked_group": source.linked_group,
                "metadata": source.metadata,
            }
            if any(row[name] != value for name, value in expected.items()):
                raise ValueError(f"retained source receipt changed: {source.input_id}")
        return by_id

    def cached(self, sources: list[Source], parser_key: str) -> tuple[Extraction, str] | None:
        if not sources:
            return None
        key = semantic_key(sources, parser_key)
        contexts = sorted((_source_context(s) for s in sources), key=_json)
        with self.connect() as conn:
            self._retained(conn, sources)
            rows = conn.execute(
                """SELECT attempt_id,result,source_ids FROM ingest_attempts
                WHERE semantic_key=%s AND parser_key=%s AND error IS NULL AND result IS NOT NULL
                ORDER BY created_at DESC, attempt_id DESC""",
                (key, parser_key),
            ).fetchall()
            if not rows:
                return None
            current_items = _source_items(sources)
            for row in rows:
                old_sources = conn.execute(
                    """SELECT input_id,sha256,media_type,namespace,url,metadata,linked_group
                    FROM ingest_sources WHERE input_id=ANY(%s)""",
                    (row["source_ids"],),
                ).fetchall()
                if len(old_sources) != len(sources):
                    continue
                old_items = _row_items(old_sources)
                old_contexts = [item["context"] for item in old_items]
                if sorted(old_contexts, key=_json) != contexts:
                    continue
                mapping = _rebind_mapping(old_items, current_items)
                if mapping is None:
                    continue
                result = Extraction.model_validate(row["result"])
                if not _reusable(result):
                    continue
                return _typed_source_refs(result, mapping), row["attempt_id"]
        return None

    def save(
        self,
        sources: list[Source],
        result: Extraction | None,
        parser_key: str,
        *,
        reuse_of: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        if not sources:
            raise ValueError("at least one retained source is required")
        if (result is None) == (error is None):
            raise ValueError("supply exactly one of result or error")
        if result is not None:
            source_ids = {source.input_id for source in sources}
            for entry in result.entries:
                for ref in entry.source_refs:
                    if ref.input_id not in source_ids:
                        raise ValueError(f"entry references an unknown input: {ref.input_id}")
            for issue in result.issues:
                if issue.input_id not in source_ids:
                    raise ValueError(f"source issue references an unknown input: {issue.input_id}")
        key = semantic_key(sources, parser_key)
        result_json = result.model_dump(mode="json") if result is not None else None
        safe_error = _safe_error(error) if error is not None else None
        receipt_key = _digest(
            {
                "acquisitions": sorted(source.acquisition_key for source in sources),
                "semantic_key": key,
                "result": result_json,
                "error": safe_error,
            }
        )
        with self.connect() as conn:
            retained = self._retained(conn, sources)
            count = len(result.entries) if result is not None else 0
            attempt_id = str(uuid4())
            inserted = conn.execute(
                """INSERT INTO ingest_attempts
                (attempt_id,receipt_key,semantic_key,parser_key,source_ids,reuse_of,result,error,saved_entries)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (receipt_key) DO NOTHING
                RETURNING attempt_id, saved_entries""",
                (
                    attempt_id,
                    receipt_key,
                    key,
                    parser_key,
                    Jsonb([source.input_id for source in sources]),
                    reuse_of,
                    Jsonb(result_json) if result_json is not None else None,
                    safe_error,
                    count,
                ),
            ).fetchone()
            if inserted is None:
                prior = conn.execute(
                    "SELECT attempt_id,saved_entries FROM ingest_attempts WHERE receipt_key=%s",
                    (receipt_key,),
                ).fetchone()
                if prior is None:
                    raise ValueError("attempt receipt was not stored")
                return {"attempt_id": prior["attempt_id"], "saved_entries": prior["saved_entries"]}
            if result is not None:
                prepared = []
                for entry in result.entries:
                    refs = entry.source_refs
                    source_rows = [retained[ref.input_id] for ref in refs]
                    times = {row["retrieved_at"] for row in source_rows}
                    observed_at = (
                        next(iter(times)) if len(times) == 1 and None not in times else None
                    )
                    prepared.append((entry, refs, observed_at, _identity_key(entry, source_rows)))
                identity_counts = Counter(
                    identity for _, _, _, identity in prepared if identity is not None
                )
                issue_inputs = {issue.input_id for issue in result.issues}
                for entry, refs, observed_at, identity_key in prepared:
                    if identity_key is not None and identity_counts[identity_key] > 1:
                        identity_key = None
                    entry_inputs = {ref.input_id for ref in refs}
                    has_reported_issues = bool(entry.issues) or bool(issue_inputs & entry_inputs)
                    meaning = _meaning(entry)
                    entry_id = str(uuid4())
                    conn.execute(
                        """INSERT INTO ingest_entry_rows
                        (entry_id,attempt_id,title,description,native_id,variant_id,sku,url,seller,location,
                         condition,availability,attributes,source_dates,issues,identity_key,semantic_hash,
                         observed_at,has_reported_issues)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (
                            entry_id,
                            attempt_id,
                            entry.title,
                            entry.description,
                            entry.native_id,
                            entry.variant_id,
                            entry.sku,
                            entry.url,
                            entry.seller,
                            entry.location,
                            entry.condition,
                            entry.availability,
                            Jsonb(entry.attributes),
                            Jsonb([d.model_dump(mode="json") for d in entry.source_dates]),
                            Jsonb(entry.issues),
                            identity_key,
                            _digest(meaning),
                            observed_at,
                            has_reported_issues,
                        ),
                    )
                    for ref in refs:
                        conn.execute(
                            "INSERT INTO ingest_entry_sources(entry_id,input_id,locator) VALUES (%s,%s,%s)",
                            (entry_id, ref.input_id, ref.locator or ""),
                        )
                    for ordinal, price in enumerate(entry.prices):
                        conn.execute(
                            """INSERT INTO ingest_price_rows
                            (entry_id,ordinal,amount,currency,original,kind,basis,conditions,source_refs)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            (
                                entry_id,
                                ordinal,
                                price.amount,
                                price.currency,
                                price.original,
                                price.kind,
                                price.basis,
                                price.conditions,
                                Jsonb([ref.model_dump(mode="json") for ref in price.source_refs]),
                            ),
                        )
            return {"attempt_id": attempt_id, "saved_entries": count}

    def query(self, view: str, **filters: Any) -> list[dict[str, Any]]:
        if view not in VIEWS:
            raise ValueError(f"unknown storage view: {view}")
        allowed = {
            "attempt_id",
            "entry_id",
            "input_id",
            "namespace",
            "native_id",
            "variant_id",
            "seller",
            "parser_key",
            "reuse_of",
            "error",
            "observed_at",
            "identity_key",
        }
        unknown = set(filters) - allowed
        if unknown:
            raise ValueError(f"unsupported query filters: {', '.join(sorted(unknown))}")
        clauses: list[str] = []
        params: list[Any] = []
        for name, value in filters.items():
            if name in {"namespace", "native_id", "variant_id", "seller", "identity_key"}:
                clauses.append(f"{name} = %s")
            elif name == "input_id":
                clauses.append("input_ids @> %s::jsonb")
                params.append(json.dumps([value]))
                continue
            else:
                clauses.append(f"{name} = %s")
            params.append(value)
        sql = f"SELECT * FROM ingest_{view}"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY 1"
        with self.connect() as conn:
            return conn.execute(sql, params).fetchall()


def _identity_key(entry: Entry, source_rows: list[dict[str, Any]]) -> str | None:
    if not entry.native_id:
        return None
    namespaces = {row["namespace"] for row in source_rows}
    if len(namespaces) != 1 or None in namespaces:
        return None
    return _digest(
        {
            "namespace": next(iter(namespaces)),
            "native_id": entry.native_id,
            "variant_id": entry.variant_id,
            "sku": entry.sku,
            "seller": entry.seller,
        }
    )
