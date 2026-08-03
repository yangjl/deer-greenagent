"""Structural IO boundary consumed by the DBTL supervisor graph."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from inspect import isawaitable
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


class DesignExecutionPort(Protocol):
    """Subset needed by Design preflight and execution."""

    def known_models(self) -> Sequence[str]: ...
    async def preview_council(self, **kwargs: Any) -> CouncilPlan | None: ...
    async def execute(
        self,
        *,
        depth: CouncilDepth | None = None,
        council_plan: CouncilPlan | None = None,
        council_proposal: CouncilProposal | None = None,
        participant_settings: Mapping[str, ParticipantSettings] | None = None,
        **kwargs: Any,
    ) -> LiveStageResult: ...


class TestReviewPort(Protocol):
    """Subset used by the Test cards and server-bound outcome write."""

    async def test_review_snapshot(self, **kwargs: Any) -> Mapping[str, Any] | None: ...
    async def record_test_outcome(self, **kwargs: Any) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class CompatibleStagePort:
    """Give older test/deployment adapters explicit optional capabilities.

    Capability probing is centralized here.  The supervisor talks to one
    stable structural port; an omitted optional method has a defined neutral
    result instead of being rediscovered through scattered ``getattr`` calls.
    """

    delegate: Any

    def known_models(self) -> Sequence[str]:
        reader = getattr(self.delegate, "known_models", None)
        return tuple(reader() or ()) if callable(reader) else ()

    async def _optional(self, name: str, **kwargs: Any) -> Any:
        method = getattr(self.delegate, name, None)
        if not callable(method):
            return None
        result = method(**kwargs)
        return await result if isawaitable(result) else result

    async def validate_stage_handoff(self, **kwargs: Any) -> str | None:
        method = getattr(self.delegate, "validate_stage_handoff", None)
        if not callable(method):
            return "This runtime cannot validate the next-stage prompt against current cycle state. Nothing was started."
        result = method(**kwargs)
        return await result if isawaitable(result) else result

    async def parked_design_context(self, **kwargs: Any) -> Any:
        return await self._optional("parked_design_context", **kwargs)

    async def active_cycle_status(self, **kwargs: Any) -> Any:
        return await self._optional("active_cycle_status", **kwargs)

    async def recover_paused_build_control(self, **kwargs: Any) -> Any:
        return await self._optional("recover_paused_build_control", **kwargs)

    async def preview_council(self, **kwargs: Any) -> CouncilPlan | None:
        return await self._optional("preview_council", **kwargs)

    async def test_review_snapshot(self, **kwargs: Any) -> Mapping[str, Any] | None:
        return await self._optional("test_review_snapshot", **kwargs)

    async def consume_feedback_request(self, **kwargs: Any) -> Any:
        return await self._optional("consume_feedback_request", **kwargs)

    async def bind_feedback_request(self, **kwargs: Any) -> Any:
        return await self._optional("bind_feedback_request", **kwargs)

    def execute(self, **kwargs: Any) -> Any:
        return self.delegate.execute(**kwargs)

    def record_test_outcome(self, **kwargs: Any) -> Any:
        method = getattr(self.delegate, "record_test_outcome", None)
        if not callable(method):
            raise AttributeError("record_test_outcome")
        return method(**kwargs)


def compatible_stage_port(value: Any) -> StageExecutionPort:
    """Normalize a production adapter or a narrow legacy test double."""
    return value if isinstance(value, CompatibleStagePort) else CompatibleStagePort(value)  # type: ignore[return-value]
