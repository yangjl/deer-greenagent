"""Structural IO boundary consumed by the DBTL supervisor graph."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from langchain_core.runnables import RunnableConfig

from deerflow.agents.dbtl.live_stage.types import LiveStageResult
from deerflow.dbtl.council import CouncilDepth, CouncilPlan
from deerflow.dbtl.council_proposal import CouncilProposal
from deerflow.dbtl.council_settings import ParticipantSettings


@runtime_checkable
class StageExecutionPort(Protocol):
    """The complete, narrow stage API used by the conversation controller."""

    def known_models(self) -> Sequence[str]: ...

    async def validate_stage_handoff(
        self,
        *,
        project_id: str | None,
        cycle_id: str,
        expected_db_revision: int,
        expected_stage: str,
    ) -> str | None: ...

    async def parked_design_context(self, *, project_id: str | None, cycle_id: str | None) -> Any: ...

    async def active_cycle_status(self, *, project_id: str) -> Sequence[Mapping[str, Any]]: ...

    async def conversation_cycle_status(self, *, project_id: str, thread_id: str) -> Mapping[str, Any] | None: ...

    async def recover_paused_build_control(self, **kwargs: Any) -> Mapping[str, Any] | None: ...

    async def preview_council(
        self,
        *,
        project_id: str | None,
        cycle_id: str | None,
        request_text: str,
        config: RunnableConfig,
        adjustment: str | None,
        depth: CouncilDepth | None = None,
        proposal: CouncilProposal | None = None,
        participant_settings: Mapping[str, ParticipantSettings] | None = None,
    ) -> CouncilPlan | None: ...

    async def execute(
        self,
        *,
        project_id: str | None,
        cycle_id: str | None,
        request_text: str,
        state: dict[str, Any],
        config: RunnableConfig,
        **kwargs: Any,
    ) -> LiveStageResult: ...

    async def test_review_snapshot(self, *, project_id: str | None, cycle_id: str | None) -> Mapping[str, Any] | None: ...

    async def record_test_outcome(
        self,
        *,
        project_id: str,
        cycle_id: str,
        recommendation: str,
        snapshot: Mapping[str, Any],
        config: RunnableConfig,
        idempotency_key: str,
    ) -> Mapping[str, Any]: ...

    async def consume_feedback_request(self, **kwargs: Any) -> Any: ...

    async def bind_feedback_request(self, **kwargs: Any) -> Any: ...
