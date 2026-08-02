"""Run event storage configuration.

Controls where run events (messages + execution traces) are persisted.

Backends:
- memory: In-memory storage, data lost on restart. Suitable for
  development and testing.
- db: SQL database via SQLAlchemy ORM. Provides full query capability.
  Suitable for production deployments.
- jsonl: Append-only JSONL files. Lightweight alternative for
  single-node deployments that need persistence without a database.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RunEventsConfig(BaseModel):
    backend: Literal["memory", "db", "jsonl"] = Field(
        default="memory",
        description="Storage backend for run events. 'memory' for development (no persistence), 'db' for production (SQL queries), 'jsonl' for lightweight single-node persistence.",
    )
    max_trace_content: int = Field(
        default=10240,
        description="Maximum trace content size in bytes before truncation (db backend only).",
    )
    track_token_usage: bool = Field(
        default=True,
        description="Whether RunJournal should accumulate token counts to RunRow.",
    )
    agent_activity_visibility: bool = Field(
        default=False,
        description=(
            "Whether the project rail shows the live runtime-activity block. Off by default: the rows are always "
            "recorded, and this gates only whether a person is shown them, so a rollout can be reversed without "
            "losing the history it produced."
        ),
    )
    #: Activity rows are the highest-frequency event type in the system and, like
    #: every other run event, nothing prunes them — ``run_events`` has no TTL and
    #: no expiry, and this cap is the first one. Zero disables it. It bounds what
    #: a *reader* is served rather than deleting rows, because a projection that
    #: quietly discarded audit-adjacent history would be a larger claim than a
    #: visibility feature should make.
    activity_page_limit: int = Field(
        default=200,
        ge=1,
        le=1000,
        description="Maximum activity rows returned in one page of the conversation activity endpoint.",
    )
