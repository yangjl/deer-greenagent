"""Configuration for GreenAgent's project-scoped DBTL workflow."""

from typing import Literal

from pydantic import BaseModel, Field

DbtlMode = Literal["disabled", "audit_only", "manual", "graph_enabled"]


class DbtlConfig(BaseModel):
    """Controls DBTL visibility and mutation boundaries.

    Phase 0 intentionally defaults to ``audit_only``: operators can inspect
    legacy records and readiness, while workflow mutations and LangGraph
    execution remain fail-closed.
    """

    mode: DbtlMode = Field(
        default="audit_only",
        description="DBTL operating mode: disabled, audit_only, manual, or graph_enabled.",
    )
    policy_version: str = Field(
        default="greenagent-dbtl-v2-draft",
        description="Vocabulary/policy contract shown in the readiness report.",
    )

    @property
    def mutations_enabled(self) -> bool:
        return self.mode in {"manual", "graph_enabled"}

    @property
    def graph_execution_enabled(self) -> bool:
        return self.mode == "graph_enabled"
