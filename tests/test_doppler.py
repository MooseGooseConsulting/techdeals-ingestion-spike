"""Reject a connection to the wrong Neon branch or database before running a child."""

import runpy

import pytest

check_target = runpy.run_path("scripts/with_doppler.py")["check_target"]
TARGET = {"host": "ep-example.example.neon.tech", "database": "ingestion_spike"}


@pytest.mark.parametrize("pooled", [False, True])
def test_direct_and_pooled_connections_must_match_target(pooled):
    host = "ep-example-pooler.example.neon.tech" if pooled else TARGET["host"]
    check_target(f"postgresql://{host}/ingestion_spike?sslmode=require", TARGET, pooled=pooled)
    for wrong in (
        f"postgresql://{host}/other_database",
        "postgresql://ep-other.example.neon.tech/ingestion_spike",
        "postgresql://ep-other-pooler.example.neon.tech/ingestion_spike",
    ):
        with pytest.raises(RuntimeError, match="outside the configured exercise target"):
            check_target(wrong, TARGET, pooled=pooled)
