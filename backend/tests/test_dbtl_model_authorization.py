"""DBTL surfaces enforce the same ``model:use`` policy as the composer path.

The lead agent authorizes the composer's resolved model, but DBTL selects
models of its own — council seats, the participant card's pickers, and the
setup/intent/roster/assessment one-shot calls. These tests pin that a role
denied a model cannot reach it through any of those side channels.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from deerflow.agents.dbtl.model_access import authorize_model_use, filter_authorized_model_names, principal_context
from deerflow.config.authorization_config import AuthorizationConfig, AuthorizationProviderConfig

RBAC_PATH = "deerflow.authz.rbac:RbacAuthorizationProvider"


def _app_config(model_names: list[str], *, roles: dict | None = None, enabled: bool = True, fail_closed: bool = True, default_role: str = "user") -> SimpleNamespace:
    provider = None
    if roles is not None:
        provider = AuthorizationProviderConfig(use=RBAC_PATH, config={"roles": roles})
    return SimpleNamespace(
        models=[SimpleNamespace(name=name) for name in model_names],
        authorization=AuthorizationConfig(
            enabled=enabled,
            fail_closed=fail_closed,
            default_role=default_role,
            provider=provider,
        ),
    )


class TestFilterAuthorizedModelNames:
    def test_disabled_authorization_returns_names_unchanged(self):
        config = _app_config(["gpt-4", "claude-3"], enabled=False)
        assert filter_authorized_model_names(("gpt-4", "claude-3"), context={}, app_config=config) == ("gpt-4", "claude-3")

    def test_restricted_role_sees_only_its_allowed_models(self):
        config = _app_config(
            ["gpt-4", "claude-3"],
            roles={"user": {"models": {"allow": ["claude-3"]}}},
        )
        assert filter_authorized_model_names(("gpt-4", "claude-3"), context={"user_id": "u1"}, app_config=config) == ("claude-3",)

    def test_provider_resolution_failure_fails_closed_to_empty(self):
        config = _app_config(["gpt-4"], enabled=True)  # enabled with no provider configured
        assert filter_authorized_model_names(("gpt-4",), context={}, app_config=config) == ()

    def test_provider_resolution_failure_fails_open_when_configured(self):
        config = _app_config(["gpt-4"], enabled=True, fail_closed=False)
        assert filter_authorized_model_names(("gpt-4",), context={}, app_config=config) == ("gpt-4",)

    def test_missing_authorization_section_is_a_no_op(self):
        config = SimpleNamespace(models=[SimpleNamespace(name="gpt-4")])
        assert filter_authorized_model_names(("gpt-4",), context={}, app_config=config) == ("gpt-4",)


class TestAuthorizeModelUse:
    def test_disabled_authorization_is_a_no_op(self):
        config = _app_config(["gpt-4"], enabled=False)
        assert authorize_model_use("gpt-4", context={}, app_config=config) == "gpt-4"

    def test_denied_model_falls_back_to_an_allowed_one(self):
        config = _app_config(
            ["gpt-4", "claude-3"],
            roles={"user": {"models": {"allow": ["claude-3"]}}},
        )
        assert authorize_model_use("gpt-4", context={"user_id": "u1"}, app_config=config) == "claude-3"

    def test_no_allowed_model_raises_when_fail_closed(self):
        config = _app_config(
            ["gpt-4", "claude-3"],
            roles={"user": {"models": {"allow": []}}},
        )
        with pytest.raises(ValueError):
            authorize_model_use("gpt-4", context={"user_id": "u1"}, app_config=config)

    def test_allowed_model_is_returned_unchanged(self):
        config = _app_config(
            ["gpt-4", "claude-3"],
            roles={"user": {"models": {"allow": ["gpt-4", "claude-3"]}}},
        )
        assert authorize_model_use("gpt-4", context={"user_id": "u1"}, app_config=config) == "gpt-4"


class TestPrincipalContext:
    def test_reads_configurable_nested_and_top_level_context(self):
        merged = principal_context(
            {
                "configurable": {"user_id": "from-configurable", "context": {"user_role": "scientist"}},
                "context": {"user_id": "from-context"},
            }
        )
        assert merged["user_id"] == "from-context"
        assert merged["user_role"] == "scientist"

    def test_non_mapping_input_is_empty(self):
        assert principal_context(None) == {}


class TestAdapterModelPickers:
    """The participant card's pickers list only models the principal may see.

    ``_known_models`` also feeds ``parse_participant_settings``' edit
    validation and the council-default check, so filtering it covers every
    surface the preflight card exposes.
    """

    def _adapter(self, app_config, runtime_config=None):
        from deerflow.agents.dbtl.live_stage.adapter import LiveStageAdapter

        return LiveStageAdapter(repo=None, app_config=app_config, runtime_config=runtime_config)

    def test_pickers_exclude_models_the_role_may_not_see(self):
        config = _app_config(
            ["gpt-4", "claude-3"],
            roles={"scientist": {"models": {"allow": ["claude-3"]}}},
            default_role="scientist",
        )
        adapter = self._adapter(config, runtime_config={"context": {"user_id": "u1", "user_role": "scientist"}})
        assert adapter.known_models() == ("claude-3",)

    def test_pickers_are_unfiltered_when_authorization_is_disabled(self):
        config = _app_config(["gpt-4", "claude-3"], enabled=False)
        adapter = self._adapter(config)
        assert adapter.known_models() == ("gpt-4", "claude-3")
