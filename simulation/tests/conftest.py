from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT),
    str(ROOT / "packages" / "core"),
    str(ROOT / "packages" / "observability"),
]

from devfactory_core import Principal  # noqa: E402
from devfactory_observability import EventLog  # noqa: E402
from simulation.flows import job_factory  # noqa: E402

TENANT = "acme"
WORKSPACE = "ws-core"


@pytest.fixture
def owner() -> Principal:
    return Principal("human", "alice", display_name="Alice")


@pytest.fixture
def reviewer() -> Principal:
    return Principal("human", "bob", display_name="Bob")


@pytest.fixture
def clock():
    """Monotonic fake clock, so trail ordering does not depend on wall time."""
    start = datetime(2026, 8, 19, 9, 0, tzinfo=timezone.utc)
    state = {"n": 0}

    def tick() -> datetime:
        state["n"] += 1
        return start + timedelta(seconds=state["n"])

    return tick


@pytest.fixture
def new(owner, clock):
    return job_factory(
        tenant_id=TENANT, workspace_id=WORKSPACE, principal=owner, clock=clock
    )


@pytest.fixture
def log() -> EventLog:
    return EventLog()
