from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.deps import get_config
from app.gateway.routers import features


def _app_with_config(
    *,
    agents_api_enabled: bool,
    browser_enabled: bool = False,
    browser_extra: dict | None = None,
    dbtl_mode: str = "audit_only",
) -> FastAPI:
    app = FastAPI()
    app.include_router(features.router)
    tools = (
        [
            SimpleNamespace(name="browser_navigate", model_extra=browser_extra or {}),
        ]
        if browser_enabled
        else []
    )
    fake_config = SimpleNamespace(
        agents_api=SimpleNamespace(enabled=agents_api_enabled),
        tools=tools,
        dbtl=SimpleNamespace(
            mode=dbtl_mode,
            mutations_enabled=dbtl_mode in {"manual", "graph_enabled"},
            graph_execution_enabled=dbtl_mode == "graph_enabled",
            design_deck_feedback=True,
            progressive_gate=False,
            policy_version="greenagent-dbtl-v2-draft",
        ),
    )
    app.dependency_overrides[get_config] = lambda: fake_config
    return app


def test_features_reports_agents_api_enabled() -> None:
    with TestClient(_app_with_config(agents_api_enabled=True)) as client:
        response = client.get("/api/features")
    assert response.status_code == 200
    assert response.json() == {
        "agents_api": {"enabled": True},
        "browser_control": {"enabled": False},
        "dbtl": {
            "mode": "audit_only",
            "mutations_enabled": False,
            "graph_execution_enabled": False,
            "design_deck_feedback": True,
            "progressive_gate": False,
            "reason": "DBTL records are available for inspection, but all DBTL mutations and graph execution are disabled.",
        },
    }


def test_features_reports_agents_api_disabled() -> None:
    with TestClient(_app_with_config(agents_api_enabled=False)) as client:
        response = client.get("/api/features")
    assert response.status_code == 200
    assert response.json()["agents_api"] == {"enabled": False}


def test_features_reports_dbtl_graph_execution_only_in_graph_enabled_mode() -> None:
    with TestClient(_app_with_config(agents_api_enabled=True, dbtl_mode="manual")) as client:
        manual = client.get("/api/features")
    with TestClient(_app_with_config(agents_api_enabled=True, dbtl_mode="graph_enabled")) as client:
        graph_enabled = client.get("/api/features")

    assert manual.json()["dbtl"]["mutations_enabled"] is True
    assert manual.json()["dbtl"]["graph_execution_enabled"] is False
    assert graph_enabled.json()["dbtl"]["graph_execution_enabled"] is True


def test_features_reports_browser_control_enabled_when_configured_and_runtime_available() -> None:
    with (
        patch("app.gateway.browser_capability.importlib.util.find_spec", return_value=object()),
        TestClient(_app_with_config(agents_api_enabled=True, browser_enabled=True)) as client,
    ):
        response = client.get("/api/features")
    assert response.status_code == 200
    assert response.json()["browser_control"] == {"enabled": True}


def test_features_reports_browser_control_disabled_when_runtime_missing() -> None:
    with (
        patch("app.gateway.browser_capability.importlib.util.find_spec", return_value=None),
        TestClient(_app_with_config(agents_api_enabled=True, browser_enabled=True)) as client,
    ):
        response = client.get("/api/features")
    assert response.status_code == 200
    assert response.json()["browser_control"] == {"enabled": False}


def test_features_reports_browser_control_disabled_for_unguarded_cdp() -> None:
    with (
        patch("app.gateway.browser_capability.importlib.util.find_spec", return_value=object()),
        TestClient(
            _app_with_config(
                agents_api_enabled=True,
                browser_enabled=True,
                browser_extra={"cdp_url": "http://127.0.0.1:9222"},
            ),
        ) as client,
    ):
        response = client.get("/api/features")
    assert response.status_code == 200
    assert response.json()["browser_control"] == {"enabled": False}
