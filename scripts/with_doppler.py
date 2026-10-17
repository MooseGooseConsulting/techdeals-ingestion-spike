"""Inject the exercise's two existing Doppler credentials into a child process.

Connection strings and keys are captured locally, never written or printed. The
committed Neon target identifies the isolated branch and fresh database explicitly.
"""

import os
import subprocess
import sys
import tomllib
from pathlib import Path
from urllib.parse import urlsplit


def captured(command):
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Credential command timed out") from None
    if result.returncode:
        raise RuntimeError(f"Credential command failed with exit {result.returncode}")
    return result.stdout.strip()


def check_target(connection, target, *, pooled=False):
    parsed = urlsplit(connection)
    host = target["host"]
    if pooled:
        endpoint, domain = host.split(".", 1)
        host = endpoint + "-pooler." + domain
    if parsed.hostname != host or parsed.path != "/" + target["database"]:
        raise RuntimeError("Neon returned a connection outside the configured exercise target")


def main():
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python scripts/with_doppler.py COMMAND [ARGS...]")
    root = Path(__file__).resolve().parents[1]
    target = tomllib.loads((root / "config/neon.toml").read_text())
    env = os.environ.copy()
    env["OPENROUTER_API_KEY"] = captured(
        [
            "doppler",
            "secrets",
            "get",
            "OPENROUTER_API_KEY",
            "--plain",
            "--project",
            "ai-automation",
            "--config",
            "dev",
        ]
    )
    base = [
        "doppler",
        "run",
        "--project",
        "apps-data",
        "--config",
        "dev",
        "--",
        "neon",
        "connection-string",
        target["branch_id"],
        "--project-id",
        target["project_id"],
        "--database-name",
        target["database"],
        "--role-name",
        target["role"],
    ]
    direct = captured(base)
    pooled = captured([*base, "--pooled"])
    check_target(direct, target)
    check_target(pooled, target, pooled=True)
    env["DATABASE_URL_UNPOOLED"] = direct
    env["DATABASE_URL"] = pooled
    env["TEST_DATABASE_URL"] = direct
    env["PYDANTIC_AI_NO_BANNER"] = "1"
    raise SystemExit(subprocess.run(sys.argv[1:], check=False, cwd=root, env=env).returncode)


if __name__ == "__main__":
    main()
