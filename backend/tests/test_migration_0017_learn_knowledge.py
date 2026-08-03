from __future__ import annotations

import importlib

import pytest


def test_0017_refuses_to_stamp_when_knowledge_claims_is_missing(monkeypatch) -> None:
    migration = importlib.import_module("deerflow.persistence.migrations.versions.0017_dbtl_learn_knowledge")
    monkeypatch.setattr(migration, "_tables", lambda: {"projects", "dbtl_cycles"})

    with pytest.raises(RuntimeError, match="requires the knowledge_claims table"):
        migration.upgrade()
