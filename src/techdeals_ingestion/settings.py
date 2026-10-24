"""Small, explicit settings for the single extraction agent."""

import hashlib
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class ParserSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    base_url: str
    model: str
    reasoning_enabled: bool = True
    reasoning_max_tokens: int = Field(gt=0)
    max_input_bytes: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    timeout_seconds: int = Field(gt=0)
    correction_retries: int = Field(ge=0, le=1)

    @classmethod
    def load(cls, path: str | Path = "config/parser.toml") -> "ParserSettings":
        with Path(path).open("rb") as source:
            return cls.model_validate(tomllib.load(source))


def fingerprint(settings: ParserSettings) -> str:
    """Fingerprint settings and the small set of code that defines their meaning."""
    package_dir = Path(__file__).resolve().parent
    repository = package_dir.parents[1]
    digest = hashlib.sha256(settings.model_dump_json().encode())
    paths = [
        package_dir / "parser.py",
        package_dir / "models.py",
        package_dir / "settings.py",
    ]
    lock = repository / "uv.lock"
    if lock.is_file():
        paths.append(lock)
    else:
        digest.update(b"uv.lock-unavailable")
    for path in paths:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()
