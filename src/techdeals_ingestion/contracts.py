"""Shared, deliberately provisional parser and trusted-input contracts."""
from __future__ import annotations
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, JsonValue

class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")

class EvidenceRef(Contract):
    capture_id: str
    locator: str

class CaptureInput(Contract):
    capture_id: str
    acquisition_key: str
    path: Path
    media_type: str
    source_namespace: str | None = None
    retrieved_at: datetime | None = None
    linked_group: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

class WorkPackage(Contract):
    package_id: str
    captures: list[CaptureInput]

class Fact(Contract):
    name: str
    value: JsonValue = None
    unit: str | None = None
    status: Literal["stated", "derived", "unknown", "ambiguous", "conflicting"] = "stated"
    original: str | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)

class Item(Contract):
    local_id: str
    title: str | None = None
    role: Literal["offered", "included", "optional", "compatible", "mentioned", "absent"]
    quantity: Decimal | None = None
    attributes: dict[str, Fact] = Field(default_factory=dict)
    evidence: list[EvidenceRef] = Field(default_factory=list)

class Price(Contract):
    local_id: str
    amount: Decimal | None = None
    currency: str | None = None
    kind: str
    original: str | None = None
    scope_refs: list[str] = Field(default_factory=list)
    quantity: Decimal | None = None
    conditions: list[Fact] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)

class Relationship(Contract):
    subject_ref: str
    predicate: str
    object_ref: str
    evidence: list[EvidenceRef] = Field(default_factory=list)

class SourceDate(Contract):
    value: str | None = None
    original_field: str
    meaning: str
    original: str | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)

class Offer(Contract):
    local_id: str
    title: str | None = None
    fields: dict[str, Fact] = Field(default_factory=dict)
    items: list[Item] = Field(default_factory=list)
    prices: list[Price] = Field(default_factory=list)
    relations: list[Relationship] = Field(default_factory=list)
    source_dates: list[SourceDate] = Field(default_factory=list)
    identity_claim: Fact | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)

class UnitResult(Contract):
    unit_id: str
    input_refs: list[EvidenceRef]
    status: Literal["complete", "partial", "no_offer", "failed"]
    offers: list[Offer] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)

class ParseBatchResult(Contract):
    batch_id: str
    units: list[UnitResult]

class PreparedUnit(Contract):
    unit_id: str
    input_refs: list[EvidenceRef]

def prepare_units(package: WorkPackage) -> list[PreparedUnit]:
    groups: dict[str, list[EvidenceRef]] = {}
    for capture in package.captures:
        key = capture.linked_group or capture.capture_id
        groups.setdefault(key, []).append(EvidenceRef(capture_id=capture.capture_id, locator="$"))
    return [PreparedUnit(unit_id=key, input_refs=refs) for key, refs in groups.items()]
