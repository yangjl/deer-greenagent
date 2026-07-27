#!/usr/bin/env python3
"""Seed a complete DBTL cycle so every stage surface has something real to show.

This exists for manual UI review. It drives the *repository*, not the tables,
so every transition passes the same gates the product enforces: Build only
opens after two independent approvals, a reconciliation row that is a human
judgement is closed by a human actor, the Test outcome is computed rather than
asserted, and Learn produces candidates that are still only candidates.

It deliberately stops before promoting anything. Promotion, publication, and
retraction are the controls under review, so the seed leaves them for a person
to exercise.

Usage (from backend/):
    PYTHONPATH=. uv run python scripts/seed_demo_dbtl_cycle.py --project-id <id>
    PYTHONPATH=. uv run python scripts/seed_demo_dbtl_cycle.py --project-id <id> --reset
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys

from deerflow.config import get_app_config
from deerflow.persistence.dbtl import DbtlCycleRepository
from deerflow.persistence.engine import close_engine, get_session_factory, init_engine_from_config

DEMO_PREFIX = "demo-dbtl"
POLICY_VERSION = "greenagent-dbtl-v2-draft"


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Seeder:
    """Walks one cycle through every stage, printing what it proved."""

    def __init__(self, repo: DbtlCycleRepository, project_id: str, user_id: str, role: str) -> None:
        self.repo = repo
        self.project_id = project_id
        self.user_id = user_id
        self.role = role
        self.cycle_id = f"{DEMO_PREFIX}-cycle-01"
        self._step = 0

    def key(self, name: str) -> str:
        return f"{DEMO_PREFIX}:{self.cycle_id}:{name}"

    def say(self, message: str) -> None:
        self._step += 1
        print(f"  {self._step:2d}. {message}")

    async def revision(self) -> int:
        cycle = await self.repo.get_cycle(self.cycle_id, project_id=self.project_id)
        if cycle is None:
            raise SystemExit(f"Cycle {self.cycle_id} vanished mid-seed.")
        return int(cycle["db_revision"])

    async def state(self) -> str:
        cycle = await self.repo.get_cycle(self.cycle_id, project_id=self.project_id)
        return str(cycle["state"]) if cycle else "<missing>"

    async def evidence(self, stage: str, artifact_type: str, body: str) -> None:
        await self.repo.attach_artifact(
            cycle_id=self.cycle_id,
            project_id=self.project_id,
            stage=stage,
            artifact_type=artifact_type,
            uri=f"/mnt/user-data/outputs/dbtl/{self.cycle_id}/{stage}/{artifact_type}.json",
            content_hash=digest(body),
            created_by=self.user_id,
            expected_db_revision=await self.revision(),
            idempotency_key=self.key(f"artifact:{stage}:{artifact_type}"),
        )

    async def approve(self, stage: str, rationale: str) -> None:
        await self.repo.submit_stage_for_review(
            cycle_id=self.cycle_id,
            project_id=self.project_id,
            stage=stage,
            expected_db_revision=await self.revision(),
            actor_user_id=self.user_id,
            idempotency_key=self.key(f"submit:{stage}"),
        )
        await self.repo.review_stage(
            cycle_id=self.cycle_id,
            project_id=self.project_id,
            stage=stage,
            decision="approve",
            rationale=rationale,
            expected_db_revision=await self.revision(),
            reviewer_user_id=self.user_id,
            reviewer_project_role=self.role,
            idempotency_key=self.key(f"review:{stage}"),
        )

    # ---------------------------------------------------------------- stages

    async def create(self) -> None:
        await self.repo.create_cycle(
            cycle_id=self.cycle_id,
            project_id=self.project_id,
            title="Demo · Plant height prediction under drought",
            cycle_class="computational",
            cycle_weight="full",
            research_question=("Does the multi-environment genomic model predict plant height in 2024 drought environments better than the single-environment baseline?"),
            objective="Compare multi-environment vs single-environment genomic prediction for plant height.",
            success_criteria=("Held-out predictive correlation improves by at least 0.05 over the baseline with no leakage between train and test environments."),
            created_by=self.user_id,
            policy_version=POLICY_VERSION,
            idempotency_key=self.key("create"),
        )
        self.say(f"Created cycle — state {await self.state()!r}")

    async def design(self) -> None:
        await self.evidence("design", "design_brief", "demo design brief v2")
        await self.approve("design", "Design is specific enough to build against: trait, environments, and the baseline are all named.")
        self.say(f"Design approved — state {await self.state()!r}")

    async def reconciliation(self) -> None:
        sources = [
            ("phenotypes_2024", "raw", "phenotype csv 2024"),
            ("genotypes_panel", "raw", "genotype matrix panel A"),
            ("weather_2024", "reference", "environment covariates 2024"),
        ]
        for source_key, role, body in sources:
            await self.repo.declare_dataset(
                cycle_id=self.cycle_id,
                project_id=self.project_id,
                source_key=source_key,
                uri=f"/mnt/user-data/workspace/data/{source_key}.csv",
                content_hash=digest(body),
                recorded_by=self.user_id,
                declared_immutable=True,
                role=role,
                expected_db_revision=await self.revision(),
                idempotency_key=self.key(f"dataset:{source_key}"),
            )
        self.say(f"Declared {len(sources)} pinned datasets (all immutable)")

        # A mix on purpose: two an agent may close, two that are human
        # judgements. The gate must refuse until the human ones are settled.
        rows = [
            ("units_and_encoding", "plant_height_cm", "agent", "Both sources report centimetres; no conversion needed."),
            ("identifier_integrity", "genotype_id", "agent", "All 412 genotype ids match the panel manifest exactly."),
            ("contradictory_sources", "plot_area_m2", "human", "Field book is authoritative over the scanned sheet for plot area."),
            ("train_test_separation", "environment_split", "human", "2024 drought sites are held out entirely; no genotype appears in both splits."),
        ]
        opened: list[tuple[str, str, str, str]] = []
        for check, field_name, actor, resolution in rows:
            row = await self.repo.open_reconciliation_row(
                cycle_id=self.cycle_id,
                project_id=self.project_id,
                check=check,
                field_name=field_name,
                created_by=self.user_id,
                source_a_label="field book",
                source_a_value="2.5",
                source_b_label="scanned sheet",
                source_b_value="2.4" if check == "contradictory_sources" else "2.5",
                required=True,
                expected_db_revision=await self.revision(),
                idempotency_key=self.key(f"row:{check}"),
            )
            opened.append((str(row["id"]), check, actor, resolution))
        self.say(f"Opened {len(opened)} reconciliation rows (2 agent-closable, 2 human judgements)")

        for row_id, check, actor, resolution in opened:
            item = next(entry for entry in (await self.repo.reconciliation_view(self.cycle_id, project_id=self.project_id))["rows"] if entry["row_id"] == row_id)
            await self.repo.decide_reconciliation_row(
                row_id=row_id,
                project_id=self.project_id,
                status="resolved",
                resolution=resolution,
                actor_type=actor,
                actor_user_id=self.user_id,
                evidence_refs=[f"artifact://{self.cycle_id}/reconciliation/{check}"],
                expected_db_revision=await self.revision(),
                expected_work_item_revision=int(item["db_revision"]),
                idempotency_key=self.key(f"decide:{check}"),
            )
        gate = await self.repo.evaluate_reconciliation(self.cycle_id, project_id=self.project_id)
        self.say(f"Every row settled — gate outcome {gate.outcome.value!r} ({gate.resolved_count}/{gate.total_required} required rows)")

        await self.evidence("reconciliation", "reconciliation_matrix", "demo matrix")
        await self.approve("reconciliation", "All four checks are settled and every input is pinned by content hash.")
        self.say(f"Reconciliation approved — state {await self.state()!r}")

    async def build(self) -> None:
        await self.repo.record_build_lineage(
            cycle_id=self.cycle_id,
            project_id=self.project_id,
            code_revision="git:9f2c1ab",
            config_revision="config:multi-env-v3",
            environment={"python": "3.12.13", "r": "4.4.1", "container": "greenagent/gs:2026.07"},
            input_artifacts=["phenotypes_2024", "genotypes_panel", "weather_2024"],
            output_artifacts=[
                {"name": "model_multi_env.rds", "uri": "/mnt/user-data/outputs/dbtl/model_multi_env.rds", "content_hash": digest("model multi env"), "revision": 1},
                {"name": "model_baseline.rds", "uri": "/mnt/user-data/outputs/dbtl/model_baseline.rds", "content_hash": digest("model baseline"), "revision": 1},
            ],
            deviations=["Dropped 3 plots with missing height measurements (documented in the matrix)."],
            logs_uri="/mnt/user-data/outputs/dbtl/build/run.log",
            recorded_by=self.user_id,
            expected_db_revision=await self.revision(),
            idempotency_key=self.key("build-lineage"),
        )
        self.say("Recorded Build lineage (dataset fingerprint, revisions, environment, outputs, deviations)")
        await self.evidence("build", "build_package", "demo build package")
        await self.approve("build", "Build is reproducible from the recorded revisions and the approved dataset.")
        self.say(f"Build approved — state {await self.state()!r}")

    async def test(self) -> str:
        await self.evidence("test", "test_report", "demo test report")
        # Test is submitted but never generically approved: the validity
        # assessment *is* its review, and the outcome is computed from the
        # evidence rather than asserted by the reviewer.
        await self.repo.submit_stage_for_review(
            cycle_id=self.cycle_id,
            project_id=self.project_id,
            stage="test",
            expected_db_revision=await self.revision(),
            actor_user_id=self.user_id,
            idempotency_key=self.key("submit:test"),
        )
        # A clean negative, which is the interesting case for Learn: the
        # comparison was methodologically sound and the answer was "no". Only
        # supported and valid-negative outcomes may advance, so this is what
        # lets the demo reach Learn with something worth promoting.
        result = await self.repo.record_validity_assessment(
            cycle_id=self.cycle_id,
            project_id=self.project_id,
            metrics=[
                {"name": "predictive_correlation", "value": 0.55, "threshold": 0.59, "criterion": "gte", "plausible_max": 0.85},
                {"name": "improvement_over_baseline", "value": 0.01, "threshold": 0.05, "criterion": "gte", "plausible_max": 0.40},
            ],
            # The full generic-predictive pack. Anything missing makes the
            # outcome inconclusive rather than a negative result, so a real
            # valid negative has to answer every check.
            checks=[
                {"check": "structure_null", "status": "passed", "detail": "Permuted-label null sits at r=0.02.", "evidence_refs": ("artifact://demo/test/null",)},
                {"check": "fold_composition", "status": "passed", "detail": "Folds are environment-disjoint.", "evidence_refs": ("artifact://demo/test/folds",)},
                {"check": "predictive_ceiling", "status": "passed", "detail": "Trait repeatability caps r at 0.85; observed 0.55 is well inside it.", "evidence_refs": ("artifact://demo/test/ceiling",)},
                {"check": "direction", "status": "passed", "detail": "Predicted and observed height move in the same direction.", "evidence_refs": ("artifact://demo/test/direction",)},
                {"check": "leakage", "status": "passed", "detail": "No genotype appears in both splits.", "evidence_refs": ("artifact://demo/test/leakage",)},
                {"check": "tester_holdout", "status": "passed", "detail": "2024 drought sites held out entirely; the result reproduces there.", "evidence_refs": ("artifact://demo/test/holdout",)},
                {"check": "within_group", "status": "passed", "detail": "Accuracy holds within each maturity group.", "evidence_refs": ("artifact://demo/test/within",)},
                {"check": "duplicates_relatedness", "status": "passed", "detail": "No duplicate genotypes; kinship between splits is below 0.25.", "evidence_refs": ("artifact://demo/test/kinship",)},
                {"check": "reproducibility", "status": "passed", "detail": "Re-ran from the recorded revisions with identical output hashes.", "evidence_refs": ("artifact://demo/test/rerun",)},
                {"check": "reconciled_inputs", "status": "passed", "detail": "Every input is pinned by content hash and reconciliation is approved.", "evidence_refs": ("artifact://demo/test/inputs",)},
            ],
            recommendation="advance_to_learn",
            limitations=["2024 environments only", "Single panel; not validated across populations"],
            rationale=("The comparison was clean and the answer is no: the improvement is +0.01 against a 0.05 success criterion. Recording a valid negative rather than reaching for a win."),
            reviewer_user_id=self.user_id,
            reviewer_project_role=self.role,
            expected_db_revision=await self.revision(),
            idempotency_key=self.key("validity"),
        )
        assessment = result.get("validity_assessment") or {}
        outcome = str(assessment.get("outcome") or "unknown")
        self.say(f"Test validity recorded — computed outcome {outcome!r} (every validity check passed; the metric simply missed its threshold)")
        self.say(f"State {await self.state()!r}")
        return outcome

    async def learn(self, outcome: str) -> None:
        await self.repo.record_learn_synthesis(
            cycle_id=self.cycle_id,
            project_id=self.project_id,
            summary=(
                "The multi-environment model did not beat the single-environment "
                "baseline on plant height: +0.01 against a 0.05 success criterion. "
                "The comparison itself was clean — inputs were pinned, folds were "
                "environment-disjoint, and every validity check passed."
            ),
            candidates=[
                {
                    "statement": ("Multi-environment genomic prediction did not improve plant-height accuracy on held-out 2024 drought environments under protocol v3."),
                    "grade": "valid_negative",
                    "limitations": ["2024 environments only", "Single panel; not validated across populations"],
                    "evidence": [
                        f"artifact://{self.cycle_id}/build/build_package",
                        f"artifact://{self.cycle_id}/test/test_report",
                    ],
                },
                {
                    "statement": ("Environment-disjoint folds are necessary for this panel: random folds overstated correlation by roughly 0.07."),
                    "grade": "methodological",
                    "limitations": ["Observed in one cycle"],
                    "evidence": [f"artifact://{self.cycle_id}/test/test_report"],
                },
            ],
            actor_user_id=f"agent:{self.user_id}",
            expected_db_revision=await self.revision(),
            idempotency_key=self.key("learn"),
        )
        view = await self.repo.knowledge_view(self.project_id, cycle_id=self.cycle_id)
        self.say(f"Learn synthesis recorded — {len(view['candidates'])} provisional candidates, 0 promoted")
        self.say(f"Final state {await self.state()!r} (outcome was {outcome!r})")


async def reset(session_factory, project_id: str) -> None:
    """Remove a previous demo cycle so the seed can be re-run cleanly."""
    from sqlalchemy import text

    async with session_factory() as session:
        for table, column in (
            ("knowledge_events", "project_id"),
            ("knowledge_publications", "source_project_id"),
            ("knowledge_claims", "project_id"),
            ("memory_candidates", "project_id"),
            ("dbtl_validity_assessments", "project_id"),
            ("dbtl_build_lineage", "project_id"),
            ("dbtl_stage_worker_runs", "project_id"),
            ("dbtl_datasets", "project_id"),
            ("dbtl_work_items", "project_id"),
            ("dbtl_artifacts", "project_id"),
            ("dbtl_activity_events", "project_id"),
            ("dbtl_human_reviews", "project_id"),
            ("dbtl_stage_attempts", "project_id"),
            ("dbtl_stage_runs", "project_id"),
            ("dbtl_cycles", "project_id"),
        ):
            try:
                await session.execute(text(f"DELETE FROM {table} WHERE {column} = :pid AND (cycle_id LIKE :like OR :always)").bindparams(pid=project_id, like=f"{DEMO_PREFIX}%", always=(table in {"dbtl_cycles"})))
            except Exception:  # noqa: BLE001,S110 — table may not exist on older schemas
                pass
        try:
            await session.execute(text("DELETE FROM dbtl_cycles WHERE project_id = :pid AND id LIKE :like").bindparams(pid=project_id, like=f"{DEMO_PREFIX}%"))
        except Exception:  # noqa: BLE001,S110
            pass
        await session.commit()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--user-id", default=None, help="Defaults to the project's first active member.")
    parser.add_argument("--reset", action="store_true", help="Delete a previous demo cycle first.")
    args = parser.parse_args()

    config = get_app_config()
    await init_engine_from_config(config.database)
    session_factory = get_session_factory()
    if session_factory is None:
        print("No SQL backend configured (database.backend must not be 'memory').", file=sys.stderr)
        return 2

    repo = DbtlCycleRepository(session_factory)

    from sqlalchemy import text

    async with session_factory() as session:
        row = (await session.execute(text("SELECT name, workspace_id FROM projects WHERE id = :pid").bindparams(pid=args.project_id))).first()
        if row is None:
            print(f"Project {args.project_id} not found.", file=sys.stderr)
            return 2
        project_name, workspace_id = row
        user_id = args.user_id
        role = "owner"
        if user_id is None:
            member = (await session.execute(text("SELECT user_id, role FROM workspace_members WHERE workspace_id = :ws AND status = 'active' ORDER BY role LIMIT 1").bindparams(ws=workspace_id))).first()
            if member is None:
                print(f"No active member in workspace {workspace_id}.", file=sys.stderr)
                return 2
            user_id, role = str(member[0]), str(member[1])

    print(f"\nSeeding a demo DBTL cycle in {project_name!r} ({args.project_id})")
    print(f"Reviewer: {user_id} ({role})\n")

    if args.reset:
        await reset(session_factory, args.project_id)
        print("  --. Removed any previous demo cycle\n")

    seeder = Seeder(repo, args.project_id, str(user_id), role)
    existing = await repo.get_cycle(seeder.cycle_id, project_id=args.project_id)
    if existing is not None:
        print(f"Demo cycle already exists in state {existing['state']!r}. Re-run with --reset to rebuild it.")
        return 0

    await seeder.create()
    await seeder.design()
    await seeder.reconciliation()
    await seeder.build()
    outcome = await seeder.test()
    await seeder.learn(outcome)

    print("\nDone. Two candidates are waiting in Learn — promotion, publication, and")
    print("retraction are left unexercised so you can drive them from the UI.\n")
    await close_engine()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
