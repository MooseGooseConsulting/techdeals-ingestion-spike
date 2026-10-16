"""Source receipts and ordinary extracted fields; application IDs stay outside model output."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue


@dataclass(frozen=True)
class Source:
    input_id: str
    acquisition_key: str
    content: bytes
    media_type: str
    namespace: str | None = None
    retrieved_at: datetime | None = None
    url: str | None = None
    linked_group: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceRef(Output):
    input_id: str
    locator: str | None = Field(
        default=None, description="Optional native locator within this supplied input."
    )


class Price(Output):
    amount: Decimal | None = Field(default=None, allow_inf_nan=False)
    currency: str | None = Field(
        default=None, description="Currency explicitly stated in the source; otherwise null."
    )
    original: str
    kind: str = Field(description="Source meaning, such as ask, bid, previous price or shipping.")
    basis: str | None = Field(
        default=None, description="Any stated per-item, lot or configuration price basis."
    )
    conditions: str | None = None


class SourceDate(Output):
    original_field: str
    value: str
    meaning: str | None = None
    object_scope: str | None = None


class Entry(Output):
    source_refs: list[SourceRef] = Field(min_length=1)
    title: str | None = None
    description: str | None = None
    native_id: str | None = None
    variant_id: str | None = None
    sku: str | None = None
    url: str | None = None
    seller: str | None = Field(default=None, description="Seller; do not substitute a vendor name.")
    location: str | None = None
    condition: str | None = None
    availability: str | None = Field(
        default=None, description="Preserve stock qualifications, not just a positive stock label."
    )
    prices: list[Price] = Field(default_factory=list)
    source_dates: list[SourceDate] = Field(default_factory=list)
    attributes: dict[str, JsonValue] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)


class SourceIssue(Output):
    input_id: str
    message: str


class Extraction(Output):
    entries: list[Entry] = Field(default_factory=list)
    issues: list[SourceIssue] = Field(default_factory=list)
