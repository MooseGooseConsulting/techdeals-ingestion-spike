"""Committed parser settings; credentials are supplied only by the process environment."""

import hashlib
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class ParserSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    base_url: str
    model: str
    reasoning_enabled: bool = True
    batch_size: int = Field(gt=0)
    max_prepared_bytes: int = Field(gt=0)
    max_package_bytes: int = Field(gt=0)
    tool_page_bytes: int = Field(gt=0)
    max_requests: int = Field(gt=0)
    max_tool_calls: int = Field(gt=0)
    context_window_tokens: int = Field(default=1000000, gt=0)
    max_output_tokens: int = Field(gt=0)
    timeout_seconds: int = Field(gt=0)
    package_timeout_seconds: int = Field(gt=0)
    correction_retries: int = Field(ge=0)

    @classmethod
    def load(cls, path: str | Path = "config/parser.toml") -> "ParserSettings":
        with Path(path).open("rb") as source:
            return cls.model_validate(tomllib.load(source))

    @property
    def config_id(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()
