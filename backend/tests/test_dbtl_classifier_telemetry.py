"""Shadow-evaluation telemetry (Phase 4).

Telemetry lives in its own table and its own repository, deliberately outside
``deerflow.persistence.dbtl``. That separation is the mechanism behind the
plan's requirement that shadow events cannot mutate cycle state: the store has
no DBTL model imported and therefore nothing to write.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import deerflow.persistence.models  # noqa: F401  — registers every table with Base.metadata
from deerflow.dbtl.classifier import ConfidenceBand
from deerflow.dbtl.proposal import ProposalOutcome
from deerflow.dbtl.routing import RouteKind, RouteSource
from deerflow.persistence.base import Base
from deerflow.persistence.telemetry import (
    ClassifierEvaluationConflict,
    ClassifierEvaluationRepository,
    ClassifierEvaluationRow,
)
from deerflow.persistence.telemetry import (
    evaluations as evaluations_module,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def repo():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield ClassifierEvaluationRepository(factory)
    finally:
        await engine.dispose()


def _payload(**overrides):
    base = {
        "evaluation_id": "eval-1",
        "project_id": "proj-1",
        "thread_id": "thread-1",
        "user_id": "user-1",
        "route_kind": RouteKind.PROPOSAL,
        "route_source": RouteSource.CLASSIFIER,
        "request_fingerprint": "a" * 64,
        "band": ConfidenceBand.HIGH,
        "confidence": 0.82,
        "rule_hits": [{"rule_id": "intent.design", "weight": 0.34, "evidence": "design"}],
        "missing_fields": ["target trait"],
        "proposed_objective": "Design and validate a genomic-selection experiment",
        "policy_version": "greenagent-dbtl-v2-draft",
    }
    return {**base, **overrides}


async def test_an_evaluation_is_recorded_without_a_human_choice(repo) -> None:
    """Shadow mode records what the system thought before anyone reacted."""
    stored = await repo.record_evaluation(**_payload())
    assert stored["evaluation_id"] == "eval-1"
    assert stored["human_choice"] is None
    assert stored["route_kind"] == "proposal"
    assert stored["band"] == "high"


async def test_recording_is_idempotent_on_the_evaluation_id(repo) -> None:
    """A retried send must not double-count the false-upgrade denominator."""
    first = await repo.record_evaluation(**_payload())
    second = await repo.record_evaluation(**_payload(confidence=0.99))
    assert second["evaluation_id"] == first["evaluation_id"]
    assert second["confidence"] == pytest.approx(0.82), "a replay must not overwrite the recorded evidence"
    assert len(await repo.list_evaluations("proj-1")) == 1


async def test_an_evaluation_key_is_bound_to_one_request(repo) -> None:
    await repo.record_evaluation(**_payload())

    with pytest.raises(ClassifierEvaluationConflict):
        await repo.record_evaluation(**_payload(request_fingerprint="b" * 64))


async def test_a_human_choice_is_attached_to_its_evaluation(repo) -> None:
    await repo.record_evaluation(**_payload())
    updated = await repo.record_outcome(
        evaluation_id="eval-1",
        project_id="proj-1",
        user_id="user-1",
        outcome=ProposalOutcome.KEEP_ORDINARY,
    )
    assert updated["human_choice"] == "keep_ordinary"
    assert updated["decided_at"] is not None


async def test_an_outcome_for_another_project_is_refused(repo) -> None:
    await repo.record_evaluation(**_payload())
    assert (
        await repo.record_outcome(
            evaluation_id="eval-1",
            project_id="proj-other",
            user_id="user-1",
            outcome=ProposalOutcome.KEEP_ORDINARY,
        )
        is None
    )


async def test_an_outcome_from_another_user_is_refused(repo) -> None:
    """One person's dismissal must not be recorded against another's card."""
    await repo.record_evaluation(**_payload())
    assert (
        await repo.record_outcome(
            evaluation_id="eval-1",
            project_id="proj-1",
            user_id="intruder",
            outcome=ProposalOutcome.DISMISSED,
        )
        is None
    )


async def test_the_first_outcome_wins(repo) -> None:
    """A card is answered once; a late second click must not rewrite history."""
    await repo.record_evaluation(**_payload())
    await repo.record_outcome(evaluation_id="eval-1", project_id="proj-1", user_id="user-1", outcome=ProposalOutcome.KEEP_ORDINARY)
    again = await repo.record_outcome(evaluation_id="eval-1", project_id="proj-1", user_id="user-1", outcome=ProposalOutcome.START_SETUP)
    assert again is not None
    assert again["human_choice"] == "keep_ordinary"


async def test_concurrent_outcomes_cannot_overwrite_each_other(repo) -> None:
    await repo.record_evaluation(**_payload())
    first, second = await asyncio.gather(
        repo.record_outcome(
            evaluation_id="eval-1",
            project_id="proj-1",
            user_id="user-1",
            outcome=ProposalOutcome.KEEP_ORDINARY,
        ),
        repo.record_outcome(
            evaluation_id="eval-1",
            project_id="proj-1",
            user_id="user-1",
            outcome=ProposalOutcome.START_SETUP,
        ),
    )
    assert first is not None and second is not None
    assert first["human_choice"] == second["human_choice"]
    assert first["human_choice"] in {"keep_ordinary", "start_setup"}


async def test_false_upgrade_and_missed_cycle_rates_are_derivable(repo) -> None:
    """The exit review needs both rates from the stored rows alone."""
    await repo.record_evaluation(**_payload(evaluation_id="e1"))
    await repo.record_outcome(evaluation_id="e1", project_id="proj-1", user_id="user-1", outcome=ProposalOutcome.KEEP_ORDINARY)

    await repo.record_evaluation(**_payload(evaluation_id="e2"))
    await repo.record_outcome(evaluation_id="e2", project_id="proj-1", user_id="user-1", outcome=ProposalOutcome.START_SETUP)

    await repo.record_evaluation(**_payload(evaluation_id="e3", route_kind=RouteKind.ORDINARY, band=ConfidenceBand.LOW, confidence=0.1))
    await repo.record_outcome(evaluation_id="e3", project_id="proj-1", user_id="user-1", outcome=ProposalOutcome.START_SETUP)

    stats = await repo.evaluation_stats("proj-1")
    assert stats["proposed"] == 2
    assert stats["classifier_ordinary"] == 1
    assert stats["false_upgrades"] == 1  # proposed, human kept it ordinary
    assert stats["missed_cycles"] == 1  # classifier said ordinary, human started a cycle anyway
    assert stats["decided"] == 3


async def test_explicit_setup_is_not_counted_as_a_classifier_miss(repo) -> None:
    await repo.record_evaluation(
        **_payload(
            evaluation_id="explicit",
            route_kind=RouteKind.CYCLE_SETUP,
            route_source=RouteSource.EXPLICIT_REQUEST,
        )
    )
    await repo.record_outcome(
        evaluation_id="explicit",
        project_id="proj-1",
        user_id="user-1",
        outcome=ProposalOutcome.START_SETUP,
    )

    stats = await repo.evaluation_stats("proj-1")
    assert stats["classifier_ordinary"] == 0
    assert stats["missed_cycles"] == 0


async def test_listing_is_scoped_to_one_project(repo) -> None:
    await repo.record_evaluation(**_payload(evaluation_id="a", project_id="proj-1"))
    await repo.record_evaluation(**_payload(evaluation_id="b", project_id="proj-2"))
    assert [row["evaluation_id"] for row in await repo.list_evaluations("proj-1")] == ["a"]


async def test_listing_is_newest_first_and_bounded(repo) -> None:
    for index in range(12):
        await repo.record_evaluation(**_payload(evaluation_id=f"e{index:02d}"))
    rows = await repo.list_evaluations("proj-1", limit=5)
    assert len(rows) == 5
    assert rows[0]["evaluation_id"] == "e11"


async def test_the_stored_row_never_holds_the_raw_user_message(repo) -> None:
    """Telemetry keeps rule hits and the objective, not the whole message.

    Chat content in a telemetry table is a quiet privacy expansion: it would
    outlive the conversation and sit outside the memory-scope controls Phase 2
    built for exactly this data.
    """
    stored = await repo.record_evaluation(**_payload())
    assert "request_text" not in stored
    assert not any("request_text" in column.name for column in ClassifierEvaluationRow.__table__.columns)


async def test_the_telemetry_store_cannot_reach_dbtl_domain_records() -> None:
    """Structural proof that a shadow event cannot mutate cycle state."""
    source = inspect.getsource(evaluations_module)
    assert "persistence.dbtl" not in source
    assert "DbtlCycle" not in source
    assert "dbtl_cycles" not in source


async def test_the_telemetry_table_is_not_a_dbtl_domain_table() -> None:
    assert ClassifierEvaluationRow.__tablename__ == "dbtl_classifier_evaluations"
    for column in ClassifierEvaluationRow.__table__.columns:
        for fk in column.foreign_keys:
            assert not fk.target_fullname.startswith("dbtl_cycles"), "telemetry must not be joined to a research record"


async def test_timestamps_are_timezone_aware(repo) -> None:
    stored = await repo.record_evaluation(**_payload())
    created = datetime.fromisoformat(stored["created_at"])
    assert created.tzinfo is not None
    assert created <= datetime.now(UTC)
