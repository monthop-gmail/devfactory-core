from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE.parent / "core")]

from devfactory_core import Job, Principal  # noqa: E402


@pytest.fixture
def alice() -> Principal:
    return Principal("human", "alice")


@pytest.fixture
def clock():
    start = datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)
    state = {"n": 0}

    def tick() -> datetime:
        state["n"] += 1
        return start + timedelta(seconds=state["n"])

    return tick


@pytest.fixture
def make_job(alice, clock):
    def build(job_id: str = "job-001", tenant_id: str = "acme", workspace_id: str = "ws-core") -> Job:
        return Job(
            job_id=job_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            principal=alice,
            clock=clock,
        )

    return build


@pytest.fixture
def external():
    """A well-formed inbound payload from another system."""

    def build(**overrides):
        payload = {
            "event_type": "SIGHTING_RECORDED",
            "tenant_id": "acme",
            "subject_type": "external",
            "subject_id": "sighting-9",
            "source": {"system": "navi-ims"},
        }
        payload.update(overrides)
        return {k: v for k, v in payload.items() if v is not None}

    return build
