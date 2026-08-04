from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from deerflow.config.dbtl_config import DbtlConfig


def test_dbtl_defaults_to_audit_only() -> None:
    config = DbtlConfig()

    assert config.mode == "audit_only"
    assert config.mutations_enabled is False
    assert config.graph_execution_enabled is False


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


def test_dbtl_build_worker_contract_defaults_to_hardened_and_accepts_rollback() -> None:
    assert DbtlConfig().build_worker_contract == "hardened_v11"
    assert DbtlConfig(build_worker_contract="hardened_v10").build_worker_contract == "hardened_v10"
    assert DbtlConfig(build_worker_contract="legacy_v9").build_worker_contract == "legacy_v9"


def test_dbtl_build_worker_contract_rejects_unknown_values() -> None:
    with pytest.raises(ValidationError):
        DbtlConfig(build_worker_contract="latest")
