"""Model-use authorization for DBTL surfaces.

The lead agent enforces ``model:use`` on the composer's resolved model
(``lead_agent.agent._authorize_model_name``), but DBTL selects models of its
own: council seats, the council default, participant-card edits, and the
setup/intent/roster/revision/assessment one-shot calls. Without these helpers
a role denied a model at the composer could still invoke it through DBTL.

Two entry points, mirroring the Gateway's ``list`` / ``use`` split:

- :func:`filter_authorized_model_names` restricts a picker list to what the
  principal's role may see (``filter_resources``) — the same semantics as the
  Gateway ``list_models`` route.
- :func:`authorize_model_use` enforces ``authorize("model", "use")`` on the
  exact name about to be dispatched, immediately before a worker or one-shot
  call. It shares the lead agent's implementation so allow/deny/fallback and
  fail-closed behavior cannot drift between the two paths.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

logger = logging.getLogger(__name__)


def principal_context(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """The merged view carrying the run's principal fields.

    A run request carries ``context`` at the top level, but LangGraph relocates
    it to ``configurable["context"]`` before a node sees it, so both shapes are
    read. ``configurable`` itself is included because the run worker also
    stamps identity fields there for legacy consumers.
    """
    if not isinstance(config, Mapping):
        return {}
    configurable = config.get("configurable")
    merged: dict[str, Any] = dict(configurable) if isinstance(configurable, Mapping) else {}
    nested = merged.get("context")
    if isinstance(nested, Mapping):
        merged.update(nested)
    context = config.get("context")
    if isinstance(context, Mapping):
        merged.update(context)
    return merged


def authorize_model_use(
    model_name: str,
    *,
    context: Mapping[str, Any] | None,
    app_config: Any,
) -> str:
    """Enforce ``model:use`` right before a DBTL one-shot or worker dispatch.

    Delegates to the lead agent's enforcement so both paths share one policy:
    an allowed name is returned unchanged, a denied one falls back to the
    first ``use``-authorized model, and fail-closed with nothing allowed
    raises ``ValueError``. Authorization disabled is a no-op.
    """
    if app_config is None or not model_name:
        return model_name
    # Read defensively: injected test app_configs may not model the
    # authorization section at all, and absent-or-disabled means "no policy".
    authz_config = getattr(app_config, "authorization", None)
    if authz_config is None or getattr(authz_config, "enabled", None) is not True:
        return model_name
    from deerflow.agents.lead_agent.agent import _authorize_model_name

    return _authorize_model_name(model_name, context=dict(context or {}), app_config=app_config)


def filter_authorized_model_names(
    names: Sequence[str],
    *,
    context: Mapping[str, Any] | None,
    app_config: Any,
) -> tuple[str, ...]:
    """Restrict a model-picker list to what the principal's role may see.

    Mirrors the Gateway ``list_models`` route: ``filter_resources`` decides
    visibility, a provider error yields an empty list (fail-closed) or the
    full list (fail-open), and disabled authorization returns the input
    unchanged. Dispatch-time ``model:use`` enforcement remains separate.
    """
    resolved = tuple(names)
    if app_config is None or not resolved:
        return resolved
    authz_config = getattr(app_config, "authorization", None)
    if authz_config is None or authz_config.enabled is not True:
        return resolved

    from deerflow.authz.principal import build_principal_from_context
    from deerflow.authz.runtime import resolve_authorization_provider

    try:
        provider = resolve_authorization_provider(authz_config)
    except Exception:
        logger.warning("Authorization provider failed to resolve while filtering DBTL model choices", exc_info=True)
        return () if authz_config.fail_closed else resolved
    if provider is None:
        return resolved

    try:
        principal = build_principal_from_context(dict(context or {}), default_role=authz_config.default_role)
        allowed = provider.filter_resources(principal, "model", list(resolved))
        if not isinstance(allowed, list) or any(not isinstance(name, str) for name in allowed):
            raise TypeError("AuthorizationProvider.filter_resources must return list[str]")
    except Exception:
        logger.warning("Authorization provider failed while filtering DBTL model choices", exc_info=True)
        return () if authz_config.fail_closed else resolved

    allowed_set = set(allowed)
    return tuple(name for name in resolved if name in allowed_set)
