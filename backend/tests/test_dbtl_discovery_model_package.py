"""Model-seeded discovery package: normaliser, schema invariant, tool policy.

Covers the structured-output slice without a model or database: the pure
normaliser closes/bounds a model-emitted package and falls back to ``None`` on
anything unusable, the schema's tool name matches the policy allow-list, and the
read-only discovery middleware lets that one tool through.
"""

from __future__ import annotations

from deerflow.dbtl.discovery import (
    DISCOVERY_PACKAGE_TOOL_NAME,
    DiscoveryProvenance,
    build_discovery_package,
    normalize_model_discovery_package,
)


def _valid_raw(**overrides: object) -> dict:
    raw = {
        "assistant_response": ("This is a good fit for DBTL because the held-out check makes the result reproducible. I propose recovering the line and verifying it on the named holdout data."),
        "proposed_title": "Recover y = 2x + 1",
        "objective": "Fit a linear model and recover the slope/intercept on holdout data.",
        "rationale": "Explicit Build/Test boundaries make the fit reproducible and auditable.",
        "intended_outputs": ["fit.py", "model.json", "holdout_metrics.json"],
        "known_inputs": ["train.csv", "holdout.csv"],
        "success_criteria": ["R^2 >= 0.99 on holdout"],
        "rejection_criteria": ["slope outside 2 +/- 0.1"],
        "conflicts": ["owner said 80/20 split earlier, then 70/30"],
        "open_questions": ["Which split ratio governs Test — 80/20 or 70/30? It changes the holdout size."],
        "accepted_fields": ["objective", "known_inputs", "intended_outputs"],
    }
    raw.update(overrides)
    return raw


def test_valid_model_package_preserves_concrete_decisions() -> None:
    payload = normalize_model_discovery_package(_valid_raw())
    assert payload is not None
    assert payload["assistant_response"].startswith("This is a good fit for DBTL")
    assert payload["objective"].startswith("Fit a linear model")
    assert payload["intended_outputs"] == ["fit.py", "model.json", "holdout_metrics.json"]
    assert payload["known_inputs"] == ["train.csv", "holdout.csv"]
    assert payload["success_criteria"] == "R^2 >= 0.99 on holdout"
    assert payload["conflicts"] == ["owner said 80/20 split earlier, then 70/30"]


def test_shape_matches_deterministic_builder_plus_conflicts() -> None:
    deterministic = build_discovery_package("fit a line y=2x+1")
    payload = normalize_model_discovery_package(_valid_raw())
    assert payload is not None
    # Every deterministic key is present so the downstream pipeline is unchanged.
    assert set(deterministic).issubset(set(payload))
    assert "conflicts" in payload  # additive, does not break the pipeline


def test_accepted_fields_drive_provenance() -> None:
    payload = normalize_model_discovery_package(_valid_raw())
    assert payload is not None
    prov = payload["provenance"]
    assert prov["objective"] == {"source": DiscoveryProvenance.USER_TURN.value, "accepted": True}
    # rationale was not listed as accepted -> tentative model suggestion.
    assert prov["rationale"] == {"source": DiscoveryProvenance.MODEL_SUGGESTION.value, "accepted": False}


def test_missing_structured_response_falls_back() -> None:
    assert normalize_model_discovery_package(None) is None
    assert normalize_model_discovery_package("not a dict") is None
    assert normalize_model_discovery_package({}) is None


def test_empty_objective_falls_back() -> None:
    assert normalize_model_discovery_package(_valid_raw(objective="   ")) is None


def test_placeholder_only_outputs_fall_back() -> None:
    raw = _valid_raw(intended_outputs=["A reviewed result for the objective"])
    assert normalize_model_discovery_package(raw) is None


def test_placeholder_outputs_are_dropped_but_real_ones_kept() -> None:
    raw = _valid_raw(intended_outputs=["a reviewed result for the objective", "model.json"])
    payload = normalize_model_discovery_package(raw)
    assert payload is not None
    assert payload["intended_outputs"] == ["model.json"]


def test_open_questions_are_bounded_to_five() -> None:
    raw = _valid_raw(open_questions=[f"material question {i}?" for i in range(9)])
    payload = normalize_model_discovery_package(raw)
    assert payload is not None
    assert len(payload["open_questions"]) == 5


def test_pydantic_model_instance_is_accepted() -> None:
    from deerflow.dbtl.discovery_schema import DbtlDiscoveryPackage

    model = DbtlDiscoveryPackage(
        assistant_response="DBTL will make the holdout decision explicit and reproducible.",
        proposed_title="t",
        objective="Recover the slope of a line.",
        rationale="reproducibility",
        intended_outputs=["model.json"],
    )
    payload = normalize_model_discovery_package(model)
    assert payload is not None
    assert payload["intended_outputs"] == ["model.json"]


def test_schema_tool_name_matches_policy_constant() -> None:
    # LangChain derives the structured tool name from the schema class name; the
    # discovery read-only middleware allows exactly that name, so the two must
    # never drift apart.
    from deerflow.dbtl.discovery_schema import DbtlDiscoveryPackage

    assert DbtlDiscoveryPackage.__name__ == DISCOVERY_PACKAGE_TOOL_NAME


def test_discovery_middleware_allows_the_package_tool() -> None:
    from deerflow.agents.middlewares.dbtl_discovery_policy_middleware import (
        _DISCOVERY_ALLOWED_TOOL_NAMES,
        DISCOVERY_READ_ONLY_TOOLS,
    )

    assert DISCOVERY_PACKAGE_TOOL_NAME in _DISCOVERY_ALLOWED_TOOL_NAMES
    # It is deliberately NOT an ordinary read-only inspection tool.
    assert DISCOVERY_PACKAGE_TOOL_NAME not in DISCOVERY_READ_ONLY_TOOLS
