"""Phase 5 — "preserve selected project and selected cycle as explicit runtime context".

The frontend chip promises the user that changing context "affects the next
request only". That promise is kept by *where* the value is read from, not by
remembering to clear it: ``configurable`` is checkpointed and would carry a
selection into later turns, while ``context`` is per request. These tests pin
that boundary, because the failure mode is silent — a cycle selected once would
keep steering a conversation the user believes has returned to ordinary work.
"""

from __future__ import annotations

import pytest

from deerflow.agents.dbtl.supervisor import (
    EXPLICIT_CHOICE_CONTEXT_KEY,
    SELECTED_CYCLE_CONTEXT_KEY,
    supervisor_context_from_config,
)
from deerflow.dbtl.routing import ExplicitChoice


class TestSelectionIsPerRequest:
    def test_selection_is_read_from_runtime_context(self):
        context = supervisor_context_from_config(
            {
                "configurable": {"thread_id": "t1"},
                "context": {
                    "project_id": "proj-1",
                    "project_name": "G2F",
                    SELECTED_CYCLE_CONTEXT_KEY: "cyc-7",
                    EXPLICIT_CHOICE_CONTEXT_KEY: "continue_cycle",
                },
            }
        )

        assert context.project_id == "proj-1"
        assert context.project_name == "G2F"
        assert context.selected_cycle_id == "cyc-7"
        assert context.explicit_choice is ExplicitChoice.CONTINUE_CYCLE

    def test_server_owned_project_lifecycle_context_is_read(self):
        context = supervisor_context_from_config(
            {
                "context": {
                    "project_id": "proj-1",
                    "dbtl_project_cycle_count": 0,
                    "dbtl_has_unfinished_cycles": False,
                }
            }
        )

        assert context.project_cycle_count == 0
        assert context.has_unfinished_cycles is False

    def test_a_selection_in_checkpointed_configurable_is_ignored(self):
        """The load-bearing test for "next request only".

        ``configurable`` persists in the checkpoint. Honouring a selection from
        there would make one click permanently scope a conversation to a cycle.
        """
        context = supervisor_context_from_config(
            {
                "configurable": {
                    "thread_id": "t1",
                    SELECTED_CYCLE_CONTEXT_KEY: "cyc-stale",
                    EXPLICIT_CHOICE_CONTEXT_KEY: "continue_cycle",
                },
                "context": {"project_id": "proj-1"},
            }
        )

        assert context.selected_cycle_id is None
        assert context.explicit_choice is None

    def test_absent_context_yields_an_unscoped_ordinary_context(self):
        context = supervisor_context_from_config({"configurable": {"thread_id": "t1"}})

        assert context.project_id is None
        assert context.selected_cycle_id is None
        assert context.explicit_choice is None

    def test_malformed_context_does_not_raise(self):
        # A non-mapping context is a client error, not a reason to fail a run.
        context = supervisor_context_from_config({"context": "not-a-mapping"})

        assert context.project_id is None
        assert context.selected_cycle_id is None


class TestUntrustedChoiceHandling:
    @pytest.mark.parametrize("raw", ["approve_everything", "", None, 7, {"a": 1}, True])
    def test_unrecognized_choices_fall_through_to_normal_routing(self, raw):
        context = supervisor_context_from_config({"context": {"project_id": "proj-1", EXPLICIT_CHOICE_CONTEXT_KEY: raw}})

        assert context.explicit_choice is None

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("ordinary", ExplicitChoice.ORDINARY),
            ("start_cycle", ExplicitChoice.START_CYCLE),
            ("continue_cycle", ExplicitChoice.CONTINUE_CYCLE),
        ],
    )
    def test_every_declared_choice_round_trips(self, raw, expected):
        context = supervisor_context_from_config({"context": {"project_id": "proj-1", EXPLICIT_CHOICE_CONTEXT_KEY: raw}})

        assert context.explicit_choice is expected
