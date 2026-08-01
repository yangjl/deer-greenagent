"""Operator-visible checks for the durable DBTL governance foundation."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.gateway.dbtl_readiness import scan_dbtl_readiness
from deerflow.config.dbtl_config import DbtlConfig
from deerflow.persistence.dbtl import DbtlGovernanceRepository

#: Provider identifiers proven to enforce nested read-only output mounts, so an
#: ordinary shell command cannot write outside the DBTL-owned output policy.
#: Unknown or unverified providers fail readiness rather than being assumed
#: safe. Add a provider only after its enforced nested mount has been verified.
_ENFORCED_OUTPUT_ISOLATION_PROVIDERS: frozenset[str] = frozenset()


def _provider_enforces_output_isolation(sandbox_provider: str) -> bool:
    return any(token and token in sandbox_provider for token in _ENFORCED_OUTPUT_ISOLATION_PROVIDERS)


def stage_output_isolation(sandbox_provider: str, *, allow_host_bash: bool) -> tuple[bool, str]:
    """Return the strict output-ownership readiness result for one provider."""
    local_provider = "LocalSandboxProvider" in sandbox_provider or "sandbox.local" in sandbox_provider
    if local_provider:
        isolated = not allow_host_bash
    else:
        isolated = _provider_enforces_output_isolation(sandbox_provider)
    if isolated:
        detail = (
            "Agent file tools enforce a read-only DBTL output mapping and local host bash is disabled."
            if local_provider
            else "The configured provider proves enforced nested read-only DBTL output mounts."
        )
    elif local_provider:
        detail = (
            "LocalSandboxProvider has sandbox.allow_host_bash=true. Host bash can bypass DBTL output ownership; "
            "disable it before cutover."
        )
    else:
        detail = (
            "This sandbox provider does not prove enforced nested read-only DBTL output mounts; "
            "strict stage-output ownership cannot be claimed."
        )
    return isolated, detail


def _check(check_id: str, title: str, passed: bool, detail: str) -> dict[str, Any]:
    return {
        "id": check_id,
        "title": title,
        "status": "passed" if passed else "blocked",
        "detail": detail,
    }


async def build_governance_report(
    *,
    repository: DbtlGovernanceRepository,
    database_backend: str,
    root: Path,
    config: DbtlConfig,
    sandbox_provider: str = "",
    allow_host_bash: bool = False,
) -> dict[str, Any]:
    """Build evidence without repairing or migrating any record."""
    schema = await repository.schema_snapshot()
    mismatches = await repository.list_projection_mismatches()
    legacy = scan_dbtl_readiness(root, config)
    postgres = database_backend == "postgres"
    schema_ready = not schema["tables_missing"] and schema["identity_binding"]
    legacy_ready = not (legacy.counts["repairable"] or legacy.counts["invalid_or_ambiguous"])
    stage_output_isolated, stage_output_detail = stage_output_isolation(
        sandbox_provider,
        allow_host_bash=allow_host_bash,
    )
    checks = [
        _check(
            "postgres-authority",
            "PostgreSQL is the durable authority",
            postgres,
            ("Connected to PostgreSQL." if postgres else f"Current backend is {database_backend}; cutover stays unavailable."),
        ),
        _check(
            "schema-parity",
            "DBTL core schema is complete",
            schema_ready,
            (f"Revision {schema['revision'] or 'create-all'} includes all governance tables." if schema_ready else f"Missing tables: {', '.join(schema['tables_missing']) or 'identity bindings'}."),
        ),
        _check(
            "project-isolation",
            "Project membership guards DBTL records",
            schema_ready,
            "DBTL records carry project_id and the review route verifies membership.",
        ),
        _check(
            "review-identity",
            "Reviewer identity is server captured",
            schema["identity_binding"],
            "Client payloads cannot supply reviewer_user_id.",
        ),
        _check(
            "replay-protection",
            "Review replay is blocked",
            schema_ready,
            "Project-scoped idempotency keys are unique and consumed once.",
        ),
        _check(
            "stale-revision",
            "Stale reviews fail closed",
            schema_ready,
            "Cycle, stage, artifact, policy, and projection revisions are bound.",
        ),
        _check(
            "projection-consistency",
            "Projection hashes match durable state",
            not mismatches,
            ("No projection mismatches detected." if not mismatches else f"{len(mismatches)} mismatch(es) require operator review."),
        ),
        _check(
            "legacy-disposition",
            "Legacy records have an explicit disposition",
            legacy_ready,
            ("No ambiguous legacy records detected." if legacy_ready else "Repairable or invalid legacy records remain; no automatic repair was attempted."),
        ),
        _check(
            "stage-output-isolation",
            "Ordinary runs cannot bypass DBTL stage output ownership",
            stage_output_isolated,
            stage_output_detail,
        ),
    ]
    technical_ready = all(check["status"] == "passed" for check in checks)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "database_backend": database_backend,
        "schema_revision": schema["revision"],
        "technical_ready": technical_ready,
        "passed_checks": sum(check["status"] == "passed" for check in checks),
        "total_checks": len(checks),
        "checks": checks,
        "projection_mismatches": mismatches,
        "legacy": {
            "counts": legacy.counts,
            "items": [item.model_dump(mode="json") for item in legacy.items],
        },
        "rollback_posture": ("Keep DBTL graph execution disabled, retain legacy files read-only, and revert to the prior application release before any data repair."),
    }
