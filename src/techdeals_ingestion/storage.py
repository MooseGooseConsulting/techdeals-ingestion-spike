"""PostgreSQL evidence retention and immutable, experiment-scoped interpretations."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .contracts import ParseBatchResult, prepare_units
from .validation import require_valid_result

VIEWS = frozenset(
    {
        "current_listings",
        "current_items",
        "current_prices",
        "listing_sightings",
        "listing_changes",
        "undated_evidence",
        "unresolved_entries",
        "ingestion_failures",
        "ingestion_outcomes",
    }
)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def semantic_key(package, unit, parser_fingerprint):
    captures = {c.capture_id: c for c in package.captures}
    parts = []
    for ref in unit.input_refs:
        c = captures[ref.capture_id]
        metadata = {
            k: v
            for k, v in c.metadata.items()
            if k not in {"retrieved_at", "acquisition_key", "capture_id", "provenance", "sha256"}
        }
        parts.append(
            [
                hashlib.sha256(c.path.read_bytes()).hexdigest(),
                c.media_type,
                c.source_namespace,
                ref.locator,
                metadata,
            ]
        )
    return digest(["contract-v2", parser_fingerprint, parts])


def _rebind(value, mapping):
    if isinstance(value, dict):
        return {
            k: mapping.get(v, v) if k == "capture_id" else _rebind(v, mapping)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_rebind(v, mapping) for v in value]
    return value


def _meaning(value):
    """Compare asserted meaning without evidence handles or model-assigned IDs."""
    if isinstance(value, dict):
        if {"local_id", "items", "prices", "relations"} <= value.keys():
            return _offer_meaning(value)
        return {
            k: v if k == "value" else _meaning(v)
            for k, v in value.items()
            if k not in {"evidence", "identity_claim", "local_id"}
        }
    if isinstance(value, list):
        return [_meaning(v) for v in value]
    return value


def _offer_meaning(offer):
    # Scope and relationship targets are substantive. Replace their arbitrary
    # names with the target's asserted content, rather than discarding the refs.
    targets = {offer["local_id"]: ["offer"]}
    targets.update({item["local_id"]: ["item", _meaning(item)] for item in offer["items"]})

    def target(ref):
        return targets.get(ref, ["unresolved", ref])

    def ordered(values):
        return sorted(values, key=lambda value: json.dumps(value, sort_keys=True, default=str))

    result = {
        k: _meaning(v)
        for k, v in offer.items()
        if k not in {"local_id", "evidence", "identity_claim", "items", "prices", "relations"}
    }
    result["items"] = ordered([_meaning(item) for item in offer["items"]])
    result["prices"] = ordered(
        [
            {**_meaning(price), "scope_refs": ordered([target(ref) for ref in price["scope_refs"]])}
            for price in offer["prices"]
        ]
    )
    result["relations"] = ordered(
        [
            {
                **_meaning(relation),
                "subject_ref": target(relation["subject_ref"]),
                "object_ref": target(relation["object_ref"]),
                "self_reference": relation["subject_ref"] == relation["object_ref"],
            }
            for relation in offer["relations"]
        ]
    )
    return result


class Store:
    def __init__(self, dsn):
        self.dsn = dsn
        self._reuse_sources = {}

    def connect(self):
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def migrate(self):
        with self.connect() as conn:
            conn.execute(Path(__file__).with_name("schema.sql").read_text())
            # Upgrade derived comparisons while retaining original payloads/evidence.
            for row in conn.execute(
                "SELECT result_id,local_id,payload,semantic_payload FROM offers"
            ).fetchall():
                meaning = _meaning(row["payload"])
                if meaning != row["semantic_payload"]:
                    conn.execute(
                        "UPDATE offers SET semantic_payload=%s WHERE result_id=%s AND local_id=%s",
                        (Jsonb(meaning), row["result_id"], row["local_id"]),
                    )

    def retain_package(self, package, index=None):
        """Commit bytes before preparation or inference; conflicting capture IDs are errors."""
        with self.connect() as conn:
            for c in package.captures:
                raw = index.originals[c.capture_id] if index else c.path.read_bytes()
                sha = hashlib.sha256(raw).hexdigest()
                conn.execute(
                    "INSERT INTO raw_content VALUES (%s,%s) ON CONFLICT DO NOTHING", (sha, raw)
                )
                values = (
                    c.capture_id,
                    c.acquisition_key,
                    sha,
                    c.media_type,
                    c.source_namespace,
                    c.retrieved_at,
                    c.linked_group,
                    Jsonb(c.metadata),
                )
                existing = conn.execute(
                    "SELECT * FROM captures WHERE capture_id=%s", (c.capture_id,)
                ).fetchone()
                if existing:
                    expected = dict(
                        zip(
                            (
                                "capture_id",
                                "acquisition_key",
                                "sha256",
                                "media_type",
                                "source_namespace",
                                "retrieved_at",
                                "linked_group",
                                "metadata",
                            ),
                            values,
                        )
                    )
                    expected["metadata"] = c.metadata
                    if existing != expected:
                        raise ValueError(
                            f"capture_id already retained with different acquisition: {c.capture_id}"
                        )
                else:
                    conn.execute("INSERT INTO captures VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", values)
        return [c.capture_id for c in package.captures]

    def reuse(self, package, index, experiment_id, arm, parser_fingerprint):
        units = prepare_units(package)
        results = []
        with self.connect() as conn:
            for unit in units:
                key = semantic_key(package, unit, parser_fingerprint)
                prior = conn.execute(
                    """SELECT i.* FROM interpretations i JOIN accepted a ON a.result_id=i.result_id
                    WHERE semantic_key=%s AND i.experiment_id=%s AND i.arm=%s
                    AND parser_fingerprint=%s AND i.payload->>'status' IN ('complete','no_offer')
                    ORDER BY (i.payload->>'status'='complete') DESC, i.result_id LIMIT 1""",
                    (key, experiment_id, arm, parser_fingerprint),
                ).fetchone()
                if not prior or prior["payload"]["status"] not in ("complete", "no_offer"):
                    return None
                old_ids = prior["input_capture_ids"]
                new_ids = [r.capture_id for r in unit.input_refs]
                if len(old_ids) != len(new_ids):
                    return None
                payload = _rebind(prior["payload"], dict(zip(old_ids, new_ids)))
                payload["unit_id"] = unit.unit_id
                results.append(payload)
                self._reuse_sources[(package.package_id, unit.unit_id, key)] = prior["result_id"]
        result = ParseBatchResult(batch_id="reuse-" + uuid4().hex, units=results)
        return require_valid_result(result, units, index)

    def publish(
        self,
        package,
        result,
        index,
        experiment_id,
        arm,
        attempt_key,
        parser_fingerprint="unspecified",
    ):
        self.retain_package(package, index)
        expected = {u.unit_id: u for u in prepare_units(package)}
        errors, valid = [], []
        seen = set()
        counts = Counter(u.unit_id for u in result.units)
        for unit in result.units:
            try:
                if counts[unit.unit_id] > 1:
                    raise ValueError("duplicate unit")
                seen.add(unit.unit_id)
                require_valid_result(
                    ParseBatchResult(batch_id=result.batch_id, units=[unit]),
                    [expected[unit.unit_id]],
                    index,
                )
                if unit.status == "failed":
                    raise ValueError("; ".join(unit.issues) or "parser failed")
                valid.append(unit)
            except (ValueError, KeyError) as exc:
                errors.append({"unit_id": unit.unit_id, "error": str(exc)})
        for missing in expected.keys() - {u.unit_id for u in result.units}:
            errors.append({"unit_id": missing, "error": "missing unit"})
        report = {"attempt_key": attempt_key, "published_units": 0, "errors": errors}
        captures = {c.capture_id: c for c in package.captures}
        with self.connect() as conn:
            prior = conn.execute(
                "SELECT * FROM attempts WHERE attempt_key=%s", (attempt_key,)
            ).fetchone()
            if prior:
                if (
                    prior["result"] != result.model_dump(mode="json")
                    or prior["experiment_id"] != experiment_id
                    or prior["arm"] != arm
                    or prior["package_id"] != package.package_id
                ):
                    raise ValueError("attempt_key collision")
                report["published_units"] = conn.execute(
                    "SELECT count(*) AS n FROM interpretations WHERE attempt_key=%s", (attempt_key,)
                ).fetchone()["n"]
                report["errors"] = prior["errors"]
                return report
            conn.execute(
                """INSERT INTO attempts(attempt_key,package_id,experiment_id,arm,result,errors)
                VALUES (%s,%s,%s,%s,%s,%s)""",
                (
                    attempt_key,
                    package.package_id,
                    experiment_id,
                    arm,
                    Jsonb(result.model_dump(mode="json")),
                    Jsonb(errors),
                ),
            )
            for unit in valid:
                try:
                    with conn.transaction():
                        cs = [captures[r.capture_id] for r in expected[unit.unit_id].input_refs]
                        dates = {c.retrieved_at for c in cs}
                        observed_at = next(iter(dates)) if len(dates) == 1 else None
                        time_status = (
                            "known"
                            if observed_at
                            else ("conflicting" if len(dates) > 1 else "unknown")
                        )
                        refs = sorted(
                            [
                                [captures[r.capture_id].acquisition_key, r.locator]
                                for r in expected[unit.unit_id].input_refs
                            ]
                        )
                        observation = digest(refs)
                        conn.execute(
                            "INSERT INTO observations VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                            (
                                observation,
                                Jsonb([c.acquisition_key for c in cs]),
                                Jsonb(refs),
                                observed_at,
                                time_status,
                            ),
                        )
                        key = semantic_key(package, expected[unit.unit_id], parser_fingerprint)
                        rid = uuid4().hex
                        conn.execute(
                            """INSERT INTO interpretations VALUES
                            (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            (
                                rid,
                                observation,
                                attempt_key,
                                experiment_id,
                                arm,
                                unit.unit_id,
                                key,
                                parser_fingerprint,
                                Jsonb(unit.model_dump(mode="json")),
                                Jsonb([c.capture_id for c in cs]),
                                self._reuse_sources.get((package.package_id, unit.unit_id, key)),
                            ),
                        )
                        for offer in unit.offers:
                            listing = self._listing(conn, offer, captures)
                            conn.execute(
                                "INSERT INTO offers VALUES (%s,%s,%s,%s,%s,%s,%s)",
                                (
                                    rid,
                                    offer.local_id,
                                    listing,
                                    offer.title,
                                    Jsonb(offer.fields.model_dump(mode="json")),
                                    Jsonb(offer.model_dump(mode="json")),
                                    Jsonb(_meaning(offer.model_dump(mode="json"))),
                                ),
                            )
                            for item in offer.items:
                                conn.execute(
                                    "INSERT INTO items VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                                    (
                                        rid,
                                        item.local_id,
                                        offer.local_id,
                                        item.title,
                                        item.role,
                                        item.quantity,
                                        Jsonb(
                                            {
                                                k: v.model_dump(mode="json")
                                                for k, v in item.attributes.items()
                                            }
                                        ),
                                        Jsonb(item.model_dump(mode="json")),
                                    ),
                                )
                            for p in offer.prices:
                                conn.execute(
                                    "INSERT INTO prices VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                                    (
                                        rid,
                                        p.local_id,
                                        offer.local_id,
                                        p.amount,
                                        p.currency,
                                        p.kind,
                                        p.quantity,
                                        Jsonb(p.scope_refs),
                                        Jsonb(p.model_dump(mode="json")),
                                    ),
                                )
                            for ordinal, relation in enumerate(offer.relations):
                                conn.execute(
                                    "INSERT INTO relations VALUES (%s,%s,%s,%s)",
                                    (
                                        rid,
                                        offer.local_id,
                                        ordinal,
                                        Jsonb(relation.model_dump(mode="json")),
                                    ),
                                )
                        # Retain every interpretation, but a partial rerun cannot erase
                        # a complete selection for the same collection event.
                        conn.execute(
                            """INSERT INTO accepted VALUES (%s,%s,%s,%s)
                            ON CONFLICT (observation_id,experiment_id,arm) DO UPDATE SET result_id=EXCLUDED.result_id
                            WHERE %s = 'complete' OR NOT EXISTS (
                                SELECT 1 FROM interpretations selected
                                WHERE selected.result_id=accepted.result_id
                                AND selected.payload->>'status'='complete')""",
                            (observation, experiment_id, arm, rid, unit.status),
                        )
                        report["published_units"] += 1
                except (psycopg.Error, ValueError) as exc:
                    errors.append({"unit_id": unit.unit_id, "error": str(exc)})
            conn.execute(
                "UPDATE attempts SET errors=%s WHERE attempt_key=%s", (Jsonb(errors), attempt_key)
            )
        return report

    def record_failure(self, package, experiment_id, arm, attempt_key, reason):
        """Retain preparation failures without requiring a successfully built evidence index."""
        self.retain_package(package)
        payload = ParseBatchResult(
            batch_id=attempt_key,
            units=[
                {
                    "unit_id": u.unit_id,
                    "input_refs": u.input_refs,
                    "status": "failed",
                    "issues": [str(reason)],
                }
                for u in prepare_units(package)
            ],
        )
        errors = [{"unit_id": u.unit_id, "error": str(reason)} for u in payload.units]
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT * FROM attempts WHERE attempt_key=%s", (attempt_key,)
            ).fetchone()
            if existing:
                if (
                    existing["package_id"] != package.package_id
                    or existing["experiment_id"] != experiment_id
                    or existing["arm"] != arm
                    or existing["result"] != payload.model_dump(mode="json")
                    or existing["errors"] != errors
                ):
                    raise ValueError("attempt_key collision")
            else:
                conn.execute(
                    "INSERT INTO attempts(attempt_key,package_id,experiment_id,arm,result,errors) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (
                        attempt_key,
                        package.package_id,
                        experiment_id,
                        arm,
                        Jsonb(payload.model_dump(mode="json")),
                        Jsonb(errors),
                    ),
                )
        return {"attempt_key": attempt_key, "published_units": 0, "errors": errors}

    def _listing(self, conn, offer, captures):
        identities = set()
        for ref in offer.evidence:
            c = captures[ref.capture_id]
            binding = c.metadata.get("identity_bindings", {}).get(ref.locator)
            if binding and binding.get("confirmed") is True:
                ns = binding.get("source_namespace", c.source_namespace)
                native = binding.get("native_id")
                if ns and native:
                    identities.add((ns, str(native), str(binding.get("variant_key") or "")))
        if len(identities) != 1:
            return None
        identity = next(iter(identities))
        lid = digest(identity)
        conn.execute(
            "INSERT INTO listings VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", (lid, *identity)
        )
        return lid

    def resolve_listing(
        self, result_id, offer_id, *, source_namespace, native_id, variant_key="", confirmed=False
    ):
        """Attach operator-confirmed identity to existing evidence, without a new sighting."""
        if confirmed is not True or not source_namespace or not native_id:
            raise ValueError("identity resolution requires confirmed source/native identity")
        identity = (source_namespace, str(native_id), variant_key or "")
        lid = digest(identity)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT listing_id FROM offers WHERE result_id=%s AND local_id=%s FOR UPDATE",
                (result_id, offer_id),
            ).fetchone()
            if not row:
                raise ValueError("unknown interpretation offer")
            if row["listing_id"] is not None and row["listing_id"] != lid:
                raise ValueError("offer already bound to another identity")
            conn.execute(
                "INSERT INTO listings VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", (lid, *identity)
            )
            conn.execute(
                "UPDATE offers SET listing_id=%s WHERE result_id=%s AND local_id=%s",
                (lid, result_id, offer_id),
            )
        return lid

    def query(self, name, *, experiment_id=None, arm=None):
        if name not in VIEWS:
            raise ValueError("unknown read view")
        filters, args = [], []
        for column, value in [("experiment_id", experiment_id), ("arm", arm)]:
            if value is not None:
                filters.append(column + "=%s")
                args.append(value)
        sql = "SELECT * FROM " + name + (" WHERE " + " AND ".join(filters) if filters else "")
        with self.connect() as conn:
            return conn.execute(sql, args).fetchall()
