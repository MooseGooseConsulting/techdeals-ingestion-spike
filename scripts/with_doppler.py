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
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"Credential command failed with exit {result.returncode}")
    return result.stdout.strip()


def main():
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python scripts/with_doppler.py COMMAND [ARGS...]")
    root = Path(__file__).resolve().parents[1]
    target = tomllib.loads((root / "config/neon.toml").read_text())
    env = os.environ.copy()
    env["OPENROUTER_API_KEY"] = captured([
        "doppler", "secrets", "get", "OPENROUTER_API_KEY", "--plain",
        "--project", "ai-automation", "--config", "dev",
    ])
    base = ["doppler", "run", "--project", "apps-data", "--config", "dev", "--",
            "neon", "connection-string", target["branch_id"], "--project-id", target["project_id"],
            "--database-name", target["database"], "--role-name", target["role"]]
    direct = captured(base)
    parsed = urlsplit(direct)
    if parsed.hostname != target["host"] or parsed.path != "/" + target["database"]:
        raise RuntimeError("Neon returned a connection outside the configured exercise target")
    env["DATABASE_URL_UNPOOLED"] = direct
    env["DATABASE_URL"] = captured([*base, "--pooled"])
    env["TEST_DATABASE_URL"] = direct
    env["PYDANTIC_AI_NO_BANNER"] = "1"
    raise SystemExit(subprocess.run(sys.argv[1:], cwd=root, env=env).returncode)


if __name__ == "__main__":
    main()
