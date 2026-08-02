"""Deployment switches that change DBTL gate prerequisites.

One reader, so the state machine, the route menu, and the Build lineage writer
cannot disagree about the rule. Kept out of ``cycle_state`` and ``stage_routes``
deliberately: those are pure values that take the answer as an argument, which
is what makes them testable in both modes without touching config.

Fail-safe is **strict**. An unreadable config keeps the gate rather than
dropping it: a deployment that cannot state its rule has not asked for the
looser one.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def reconciliation_required() -> bool:
    """True when Build waits for a settled reconciliation matrix."""
    from deerflow.config.app_config import get_app_config

    try:
        app_config = get_app_config()
    except Exception:  # noqa: BLE001 - an unreadable config keeps the stricter rule
        logger.warning("Could not read the DBTL reconciliation rule; keeping Build gated on reconciliation.", exc_info=True)
        return True
    value = getattr(getattr(app_config, "dbtl", None), "reconciliation_required", True)
    return True if value is None else bool(value)


def build_workflow_steps_enabled() -> bool:
    """True when Build review requires the durable phased workflow chain.

    An unreadable configuration fails closed. Once a Build is being submitted,
    accepting an artifact without knowing whether its workflow was required is
    less safe than asking the owner to retry after configuration is available.
    """
    from deerflow.config.app_config import get_app_config

    try:
        app_config = get_app_config()
    except Exception:  # noqa: BLE001 - an unreadable config keeps the stricter gate
        logger.warning("Could not read the DBTL Build workflow rule; requiring a complete workflow chain.", exc_info=True)
        return True
    return bool(getattr(getattr(app_config, "dbtl", None), "build_workflow_steps", False))
