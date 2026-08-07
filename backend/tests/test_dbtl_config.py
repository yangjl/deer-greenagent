from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from deerflow.config.dbtl_config import DbtlConfig
from deerflow.dbtl.policy import DBTL_POLICY_VERSION


def test_dbtl_defaults_to_audit_only() -> None:
    config = DbtlConfig()

    assert config.mode == "audit_only"
    assert config.mutations_enabled is False
    assert config.graph_execution_enabled is False
    assert config.degraded_evidence_continuation is False


def test_policy_version_is_not_operator_configurable() -> None:
    assert "policy_version" not in DbtlConfig.model_fields
    assert DBTL_POLICY_VERSION == "greenagent-dbtl-v2-draft"


def test_reconciliation_cannot_be_configured_as_a_build_gate() -> None:
    assert "reconciliation_required" not in DbtlConfig.model_fields


@pytest.mark.parametrize("mode", ["disabled", "audit_only", "manual", "graph_enabled"])
def test_dbtl_accepts_only_documented_modes(mode: str) -> None:
    assert DbtlConfig(mode=mode).mode == mode


def test_dbtl_rejects_unknown_mode() -> None:
    with pytest.raises(ValidationError):
        DbtlConfig(mode="experimental")


def test_dbtl_manual_mode_does_not_enable_graph_execution() -> None:
    config = DbtlConfig(mode="manual")

    assert config.mutations_enabled is True
    assert config.graph_execution_enabled is False


def test_dbtl_config_can_be_read_from_app_config_shape() -> None:
    app_config = SimpleNamespace(dbtl=DbtlConfig(mode="graph_enabled"))

    assert app_config.dbtl.graph_execution_enabled is True
