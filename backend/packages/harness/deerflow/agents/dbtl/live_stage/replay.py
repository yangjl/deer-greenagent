"""Idempotent stage replay, including stage-specific repair semantics."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .types import LiveStageResult

LearnSynthesis = Callable[[Sequence[Mapping[str, Any]]], tuple[str, list[dict[str, Any]]]]


@dataclass(frozen=True, slots=True)
class ReplayService:
    """Return prior evidence without dispatching workers again."""

    repo: Any

    async def resolve(
        self,
        *,
        project_id: str,
        cycle_id: str,
        user_id: str,
        execution_key: str,
        learn_synthesis: Callable[..., tuple[str, list[dict[str, Any]]]],
    ) -> LiveStageResult | None:
        replay = await self.repo.get_stage_execution_replay(
            cycle_id,
            project_id=project_id,
            idempotency_key=execution_key,
        )
        if replay is None:
            return None
        stage = str(replay.get("stage") or "unknown")
        artifact_uri = replay.get("artifact_uri")
        worker_count = int(replay.get("worker_count") or 0)
        trustworthy_count = int(replay.get("trustworthy_count") or 0)
        clarification_question = None
        if stage == "design" and trustworthy_count == 0:
            prior_runs = await self.repo.list_worker_runs(cycle_id, project_id=project_id, stage="design")
            for prior in reversed(prior_runs):
                candidate = (prior.get("result") or {}).get("clarification_question")
                if isinstance(candidate, str) and candidate.strip():
                    clarification_question = candidate.strip()
                    break
        if stage == "learn":
            knowledge = await self.repo.knowledge_view(project_id, cycle_id=cycle_id)
            if not any(event.get("event_type") == "learn.synthesized" for event in knowledge["events"]):
                prior_runs = await self.repo.list_worker_runs(cycle_id, project_id=project_id, stage="learn")
                build_test = await self.repo.build_test_view(cycle_id, project_id=project_id)
                assessment = dict((build_test or {}).get("validity_assessment") or {})
                summary, candidates = learn_synthesis(
                    [dict(item.get("result") or {}) for item in prior_runs],
                    test_outcome=str(assessment.get("outcome") or ""),
                    fallback_summary=str(artifact_uri or ""),
                )
                current = await self.repo.get_cycle(cycle_id, project_id=project_id)
                if current is not None:
                    await self.repo.record_learn_synthesis(
                        cycle_id=cycle_id,
                        project_id=project_id,
                        summary=summary,
                        candidates=candidates,
                        actor_user_id=f"agent:{user_id}",
                        expected_db_revision=int(current["db_revision"]),
                        idempotency_key=f"{execution_key}:learn",
                    )
        return LiveStageResult(
            stage=stage,
            cycle_id=cycle_id,
            note=(f"This run already recorded {worker_count} bounded {stage} worker(s)" + (f" and the review package at {artifact_uri}." if artifact_uri else "; no usable review package was produced.")),
            worker_count=worker_count,
            produced_usable_evidence=trustworthy_count > 0,
            artifact_uri=str(artifact_uri) if artifact_uri else None,
            clarification_question=clarification_question,
        )
