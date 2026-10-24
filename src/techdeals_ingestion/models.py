"""Source receipts and ordinary extracted fields; application IDs stay outside model output."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


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


_FORMATTED_PRICE = re.compile(
    r"""
    (?:(?P<prefix>[A-Z]{3}|[$€£¥])\s*)?
    (?P<sign>[-+])?
    (?P<number>
        (?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?
        |\.\d+(?:[eE][-+]?\d+)?
    )
    (?:\s*(?P<suffix>[A-Z]{3}))?
    """,
    re.VERBOSE,
)


def _formatted_price_amounts(original: str) -> list[Decimal]:
    """Every supported price embedded in the text. Free text contributes none."""
    amounts: list[Decimal] = []
    for match in _FORMATTED_PRICE.finditer(original):
        number = match.group("number").replace(",", "")
        sign = match.group("sign") or ""
        try:
            amount = Decimal(f"{sign}{number}")
        except InvalidOperation:
            continue
        if amount.is_finite():
            amounts.append(amount)
    return amounts


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceRef(Output):
    input_id: str
    locator: str | None = Field(
        default=None,
        description=(
            "Optional locator that resolves within the supplied native input. Use JSON null when "
            "the whole capture supports this entry or a precise locator is not available."
        ),
    )


class Price(Output):
    amount: Decimal | None = Field(
        default=None,
        allow_inf_nan=False,
        description="Exact stated numeric amount, or JSON null when the source gives no amount.",
    )
    currency: str | None = Field(
        default=None,
        pattern=r"^[A-Z]{3}$",
        description=(
            "A currency string must have the uppercase three-letter ISO 4217 code shape, but this "
            "format check does not establish that it is a real currency or that the source supports it. "
            "Use JSON null unless the source establishes the currency."
        ),
    )
    original: str = Field(description="Price text copied faithfully from the source.")
    kind: str = Field(description="Source meaning, such as ask, bid, previous price or shipping.")
    basis: str | None = Field(
        default=None,
        description=(
            "Stated per-item, lot, or configuration price basis, copied faithfully; JSON null if absent."
        ),
    )
    conditions: str | None = Field(
        default=None,
        description="Price conditions copied faithfully from the source; JSON null if absent.",
    )
    source_refs: list[SourceRef] = Field(
        default_factory=list,
        description=(
            "Captures and optional locators that support this price. Required when the entry cites "
            "more than one input; a single entry source already identifies the capture."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def fill_unambiguous_amount(cls, data: Any) -> Any:
        """Fill a missing amount when the original text holds precisely one.

        Deterministic normalization, not model judgment: parsing "$280" to
        280 adds no information beyond the already-copied original text.
        Ambiguous text (ranges, several amounts) keeps amount null, and the
        source-evidence checks afterward still reject anything the capture
        does not support (counts, model numbers, grams).
        """
        if isinstance(data, dict) and data.get("amount") is None:
            original = data.get("original")
            if isinstance(original, str):
                amounts = list(dict.fromkeys(_formatted_price_amounts(original)))
                if len(amounts) == 1:
                    return {**data, "amount": amounts[0]}
        return data

    @model_validator(mode="after")
    def amount_matches_plain_numeric_original(self) -> "Price":
        amounts = list(dict.fromkeys(_formatted_price_amounts(self.original)))
        if len(amounts) != 1:
            if self.amount is not None:
                raise ValueError("amount must be null when the original price is not checkable")
            return self
        if self.amount != amounts[0]:
            raise ValueError("amount must match the original price")
        return self


class SourceDate(Output):
    original_field: str = Field(
        description="Native date field name copied exactly from the source."
    )
    value: str = Field(description="Native date value copied faithfully; do not reformat or infer.")
    meaning: str | None = Field(
        default=None,
        description="Date meaning only when established by the source; otherwise JSON null.",
    )
    object_scope: str | None = Field(
        default=None,
        description=(
            "Source object this date belongs to when established, such as a listing or item; "
            "otherwise JSON null. Never use collection retrieval time here."
        ),
    )
    source_refs: list[SourceRef] = Field(
        default_factory=list,
        description=(
            "Captures and optional locators that support this date. Required when the entry cites "
            "more than one input; a single entry source already identifies the capture."
        ),
    )


class Entry(Output):
    source_refs: list[SourceRef] = Field(min_length=1)
    title: str | None = Field(
        default=None,
        description="Item or configuration title copied faithfully; JSON null if absent.",
    )
    description: str | None = Field(
        default=None,
        description="Source description copied faithfully; JSON null if absent.",
    )
    native_id: str | None = Field(
        default=None, description="Native source identifier copied exactly; JSON null if absent."
    )
    variant_id: str | None = Field(
        default=None, description="Native variant identifier copied exactly; JSON null if absent."
    )
    sku: str | None = Field(
        default=None, description="Source SKU copied exactly; JSON null if absent."
    )
    url: str | None = Field(
        default=None, description="Item URL stated by the source; JSON null if absent."
    )
    seller: str | None = Field(
        default=None,
        description=(
            "Seller copied faithfully only when the source identifies the seller. A vendor or "
            "manufacturer is not the seller. Use JSON null when seller is not established."
        ),
    )
    location: str | None = Field(
        default=None, description="Source-stated item or seller location; JSON null if absent."
    )
    condition: str | None = Field(
        default=None, description="Source-stated condition copied faithfully; JSON null if absent."
    )
    availability: str | None = Field(
        default=None,
        description=(
            "Availability and stock qualifications copied faithfully; do not infer from a purchase "
            "quantity selector. Use JSON null if the source does not establish availability."
        ),
    )
    prices: list[Price] = Field(
        default_factory=list,
        description="Every stated price for this entry, nested under the entry it describes.",
    )
    source_dates: list[SourceDate] = Field(
        default_factory=list,
        description="Native source dates with original value and object scope; never retrieval time.",
    )
    attributes: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="Source-supported specifications and other useful native fields.",
    )
    issues: list[str] = Field(
        default_factory=list,
        description="Material ambiguity, conflict, or truncation for this entry, if any.",
    )


class SourceIssue(Output):
    input_id: str
    message: str


class Extraction(Output):
    entries: list[Entry] = Field(default_factory=list)
    issues: list[SourceIssue] = Field(default_factory=list)
