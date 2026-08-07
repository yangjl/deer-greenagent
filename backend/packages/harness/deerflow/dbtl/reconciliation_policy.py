"""Deployment switches for optional DBTL execution behavior."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def conditional_test_enabled() -> bool:
    """True when a person may skip Test and take a Build straight to Learn.

    Fail-safe is **mandatory Test**, matching the other switches here: a
    deployment that cannot read its own rule has not asked for the looser one,
    and defaulting the other way would let an unreadable config turn every
    Build into an unvalidated exploratory closeout.
    """
    from deerflow.config.app_config import get_app_config

    try:
        app_config = get_app_config()
    except Exception:  # noqa: BLE001 - an unreadable config keeps Test mandatory
        logger.warning("Could not read the DBTL conditional-Test rule; keeping Test mandatory.", exc_info=True)
        return False
    return bool(getattr(getattr(app_config, "dbtl", None), "conditional_test", False))


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


def degraded_evidence_continuation_enabled() -> bool:
    """Whether a human may continue a red-flagged Build or Test exception."""
    from deerflow.config.app_config import get_app_config

    try:
        app_config = get_app_config()
    except Exception:  # noqa: BLE001 - an unreadable switch keeps hard refusal
        logger.warning("Could not read the degraded-evidence rule; keeping exception continuation disabled.", exc_info=True)
        return False
    return bool(getattr(getattr(app_config, "dbtl", None), "degraded_evidence_continuation", False))
