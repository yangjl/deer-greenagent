"""Durable Phase 8 Learn and governed-knowledge operations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from deerflow.dbtl.knowledge import (
    KnowledgeLifecycleRefused,
    candidate_eligibility,
    publication_pointer,
    validate_candidate_grade,
)
from deerflow.persistence.dbtl.model import (
    DbtlValidityAssessmentRow,
    KnowledgeClaimRow,
    KnowledgeEventRow,
    KnowledgeLinkRow,
    KnowledgePromotionRow,
    KnowledgePublicationRow,
    MemoryCandidateRow,
)
from deerflow.persistence.workspaces.model import ProjectRow


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


class KnowledgeOpsMixin:
    """Knowledge operations mixed into the cycle aggregate repository."""

    @staticmethod
    def _candidate_payload(row: MemoryCandidateRow) -> dict[str, Any]:
        content = dict(row.content or {})
        provenance = dict(row.provenance or {})
        return {
            "id": row.id,
            "project_id": row.project_id,
            "cycle_id": row.cycle_id,
            "statement": content.get("statement", ""),
            "summary": content.get("summary", ""),
            "limitations": list(content.get("limitations") or []),
            "grade": content.get("grade", ""),
            "evidence": list(provenance.get("evidence") or []),
            "test_outcome": provenance.get("test_outcome"),
            "status": row.status,
            "created_by": row.created_by,
            "created_at": _iso(row.created_at),
        }

    @staticmethod
    def _claim_payload(row: KnowledgeClaimRow) -> dict[str, Any]:
        evidence = dict(row.evidence or {})
        return {
            "id": row.id,
            "project_id": row.project_id,
            "statement": row.statement,
            "evidence": list(evidence.get("refs") or []),
            "limitations": list(evidence.get("limitations") or []),
            "review": dict(evidence.get("review") or {}),
            "rendered_uri": evidence.get("rendered_uri"),
            "source_candidate_id": evidence.get("source_candidate_id"),
            "grade": row.confidence,
            "status": row.status,
            "created_at": _iso(row.created_at),
        }

    @staticmethod
    def _publication_payload(row: KnowledgePublicationRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "claim_id": row.claim_id,
            "source_project_id": row.source_project_id,
            "target_project_id": row.target_project_id,
            "status": row.status,
            "pointer": dict(row.pointer or {}),
            "published_by": row.published_by,
            "authorization_reference": row.authorization_reference,
            "created_at": _iso(row.created_at),
            "retracted_at": _iso(row.retracted_at),
        }

    @staticmethod
    def _knowledge_event_payload(row: KnowledgeEventRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "project_id": row.project_id,
            "cycle_id": row.cycle_id,
            "candidate_id": row.candidate_id,
            "claim_id": row.claim_id,
            "publication_id": row.publication_id,
            "event_type": row.event_type,
            "actor_user_id": row.actor_user_id,
            "payload": dict(row.payload or {}),
            "created_at": _iso(row.created_at),
        }

    async def _knowledge_replay(
        self,
        session,
        project_id: str,
        key: str,
        *,
        event_type: str,
        cycle_id: str | None = None,
        candidate_id: str | None = None,
        claim_id: str | None = None,
    ):
        existing = await session.scalar(
            select(KnowledgeEventRow).where(
                KnowledgeEventRow.project_id == project_id,
                KnowledgeEventRow.idempotency_key == key,
            )
        )
        if existing is None:
            return None
        if existing.event_type != event_type or (cycle_id is not None and existing.cycle_id != cycle_id) or (candidate_id is not None and existing.candidate_id != candidate_id) or (claim_id is not None and existing.claim_id != claim_id):
            raise KnowledgeLifecycleRefused("This idempotency key was already used for a different knowledge action.")
        return existing

    @staticmethod
    def _add_knowledge_event(
        session,
        *,
        project_id: str,
        event_type: str,
        actor_user_id: str,
        idempotency_key: str,
        payload: dict[str, Any],
        cycle_id: str | None = None,
        candidate_id: str | None = None,
        claim_id: str | None = None,
        publication_id: str | None = None,
    ) -> KnowledgeEventRow:
        row = KnowledgeEventRow(
            id=f"knowledge-event-{uuid4()}",
            project_id=project_id,
            cycle_id=cycle_id,
            candidate_id=candidate_id,
            claim_id=claim_id,
            publication_id=publication_id,
            event_type=event_type,
            actor_user_id=actor_user_id,
            idempotency_key=idempotency_key,
            payload=payload,
        )
        session.add(row)
        return row

    async def knowledge_view(
        self,
        project_id: str,
        *,
        cycle_id: str | None = None,
    ) -> dict[str, Any]:
        async with self._sf() as session:
            candidate_stmt = select(MemoryCandidateRow).where(MemoryCandidateRow.project_id == project_id)
            if cycle_id is not None:
                candidate_stmt = candidate_stmt.where(MemoryCandidateRow.cycle_id == cycle_id)
            candidates = list((await session.execute(candidate_stmt.order_by(MemoryCandidateRow.created_at.asc()))).scalars())
            claims = list((await session.execute(select(KnowledgeClaimRow).where(KnowledgeClaimRow.project_id == project_id).order_by(KnowledgeClaimRow.created_at.asc()))).scalars())
            claim_ids = [row.id for row in claims]
            publications = list((await session.execute(select(KnowledgePublicationRow).where(KnowledgePublicationRow.claim_id.in_(claim_ids)).order_by(KnowledgePublicationRow.created_at.asc()))).scalars()) if claim_ids else []
            event_stmt = select(KnowledgeEventRow).where(KnowledgeEventRow.project_id == project_id)
            if cycle_id is not None:
                event_stmt = event_stmt.where(KnowledgeEventRow.cycle_id == cycle_id)
            events = list((await session.execute(event_stmt.order_by(KnowledgeEventRow.created_at.asc()))).scalars())
            return {
                "project_id": project_id,
                "cycle_id": cycle_id,
                "candidates": [self._candidate_payload(row) for row in candidates],
                "claims": [self._claim_payload(row) for row in claims],
                "publications": [self._publication_payload(row) for row in publications],
                "events": [self._knowledge_event_payload(row) for row in events],
            }

    async def record_learn_synthesis(
        self,
        *,
        cycle_id: str,
        project_id: str,
        summary: str,
        candidates: list[dict[str, Any]],
        actor_user_id: str,
        expected_db_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not summary.strip():
            raise ValueError("Learn synthesis requires a summary.")
        async with self._sf() as session:
            loaded = await self._load(session, cycle_id, project_id, for_update=True)
            if loaded is None:
                raise KnowledgeLifecycleRefused("Cycle not found.")
            cycle, stages = loaded
            if await self._knowledge_replay(
                session,
                project_id,
                idempotency_key,
                event_type="learn.synthesized",
                cycle_id=cycle_id,
            ):
                await session.rollback()
                return await self.knowledge_view(project_id, cycle_id=cycle_id)
            self._require_revision(cycle, expected_db_revision)
            if cycle.state != "learn":
                raise KnowledgeLifecycleRefused(f"Learn synthesis is unavailable while the cycle is in {cycle.state!r}.")
            assessment = await session.scalar(
                select(DbtlValidityAssessmentRow)
                .where(
                    DbtlValidityAssessmentRow.cycle_id == cycle_id,
                    DbtlValidityAssessmentRow.project_id == project_id,
                )
                .order_by(DbtlValidityAssessmentRow.assessment_revision.desc())
                .limit(1)
            )
            if assessment is None:
                raise KnowledgeLifecycleRefused("Learn requires a human-owned Test validity assessment.")
            eligibility = candidate_eligibility(assessment.outcome)
            if candidates and not eligibility.eligible:
                raise KnowledgeLifecycleRefused(eligibility.reason)

            created: list[MemoryCandidateRow] = []
            for item in candidates[:20]:
                statement = str(item.get("statement") or "").strip()
                evidence = list(item.get("evidence") or [])
                limitations = [str(value).strip() for value in list(item.get("limitations") or []) if str(value).strip()][:50]
                if not statement:
                    raise ValueError("Every Learn candidate needs a statement.")
                if not evidence:
                    raise ValueError("Every Learn candidate must reference evidence.")
                grade = validate_candidate_grade(assessment.outcome, str(item.get("grade") or ""))
                row = MemoryCandidateRow(
                    id=f"candidate-{uuid4()}",
                    project_id=project_id,
                    cycle_id=cycle_id,
                    content={
                        "statement": statement,
                        "summary": summary.strip(),
                        "limitations": limitations,
                        "grade": grade.value,
                    },
                    provenance={
                        "evidence": evidence[:50],
                        "validity_assessment_id": assessment.id,
                        "test_outcome": assessment.outcome,
                    },
                    status="proposed",
                    created_by=actor_user_id,
                )
                session.add(row)
                created.append(row)

            self._commit_revision(cycle, self._statuses(stages))
            for stage in stages:
                stage.db_revision = cycle.db_revision
            self._add_knowledge_event(
                session,
                project_id=project_id,
                cycle_id=cycle_id,
                event_type="learn.synthesized",
                actor_user_id=actor_user_id,
                idempotency_key=idempotency_key,
                payload={
                    "summary": summary.strip(),
                    "candidate_ids": [row.id for row in created],
                    "test_outcome": assessment.outcome,
                    "db_revision": cycle.db_revision,
                },
            )
            await self._record_event(
                session,
                cycle=cycle,
                event_type="learn.synthesized",
                actor_user_id=actor_user_id,
                payload={
                    "idempotency_key": idempotency_key,
                    "candidate_count": len(created),
                    "test_outcome": assessment.outcome,
                },
            )
            await session.commit()
        return await self.knowledge_view(project_id, cycle_id=cycle_id)

    async def decide_candidate(
        self,
        *,
        candidate_id: str,
        project_id: str,
        decision: str,
        actor_user_id: str,
        rationale: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        target = {"keep": "working_memory", "discard": "discarded"}.get(decision)
        if target is None:
            raise ValueError("Candidate decision must be 'keep' or 'discard'.")
        if not rationale.strip():
            raise ValueError("A candidate decision requires a rationale.")
        async with self._sf() as session:
            row = await session.scalar(
                select(MemoryCandidateRow)
                .where(
                    MemoryCandidateRow.id == candidate_id,
                    MemoryCandidateRow.project_id == project_id,
                )
                .with_for_update()
            )
            if row is None:
                raise KnowledgeLifecycleRefused("Candidate not found.")
            if await self._knowledge_replay(
                session,
                project_id,
                idempotency_key,
                event_type=f"candidate.{target}",
                candidate_id=row.id,
            ):
                replay_cycle_id = row.cycle_id
                await session.rollback()
                return await self.knowledge_view(project_id, cycle_id=replay_cycle_id)
            if row.status not in {"proposed", "working_memory"}:
                raise KnowledgeLifecycleRefused(f"Candidate is already {row.status!r}.")
            row.status = target
            self._add_knowledge_event(
                session,
                project_id=project_id,
                cycle_id=row.cycle_id,
                candidate_id=row.id,
                event_type=f"candidate.{target}",
                actor_user_id=actor_user_id,
                idempotency_key=idempotency_key,
                payload={"rationale": rationale.strip()},
            )
            await session.commit()
            cycle_id = row.cycle_id
        return await self.knowledge_view(project_id, cycle_id=cycle_id)

    async def promote_candidate(
        self,
        *,
        candidate_id: str,
        project_id: str,
        statement: str,
        grade: str,
        limitations: list[str],
        reviewer_user_id: str,
        reviewer_project_role: str,
        rationale: str,
        authorization_reference: str,
        idempotency_key: str,
        rendered_uri: str,
        claim_id: str | None = None,
        supersedes_claim_id: str | None = None,
    ) -> dict[str, Any]:
        if not statement.strip() or not rationale.strip():
            raise ValueError("Promotion requires a statement and rationale.")
        async with self._sf() as session:
            candidate = await session.scalar(
                select(MemoryCandidateRow)
                .where(
                    MemoryCandidateRow.id == candidate_id,
                    MemoryCandidateRow.project_id == project_id,
                )
                .with_for_update()
            )
            if candidate is None:
                raise KnowledgeLifecycleRefused("Candidate not found.")
            if await self._knowledge_replay(
                session,
                project_id,
                idempotency_key,
                event_type="claim.promoted",
                candidate_id=candidate.id,
            ):
                promoted_claim = await session.scalar(
                    select(KnowledgeClaimRow)
                    .join(
                        KnowledgePromotionRow,
                        KnowledgePromotionRow.knowledge_claim_id == KnowledgeClaimRow.id,
                    )
                    .where(
                        KnowledgeClaimRow.project_id == project_id,
                        KnowledgePromotionRow.memory_candidate_id == candidate.id,
                    )
                )
                promoted_claim_id = promoted_claim.id if promoted_claim is not None else None
                replay_cycle_id = candidate.cycle_id
                await session.rollback()
                view = await self.knowledge_view(project_id, cycle_id=replay_cycle_id)
                if promoted_claim_id is not None:
                    view["claim"] = next(item for item in view["claims"] if item["id"] == promoted_claim_id)
                return view
            if candidate.status not in {"proposed", "working_memory"}:
                raise KnowledgeLifecycleRefused(f"Candidate is {candidate.status!r} and cannot be promoted.")
            provenance = dict(candidate.provenance or {})
            parsed_grade = validate_candidate_grade(str(provenance.get("test_outcome") or ""), grade)
            candidate_evidence = list(provenance.get("evidence") or [])
            if not candidate_evidence:
                raise KnowledgeLifecycleRefused("A candidate without evidence cannot be promoted.")
            resolved_claim_id = claim_id or f"claim-{uuid4()}"
            claim = KnowledgeClaimRow(
                id=resolved_claim_id,
                project_id=project_id,
                statement=statement.strip(),
                evidence={
                    "refs": candidate_evidence,
                    "limitations": [item.strip() for item in limitations if item.strip()][:50],
                    "source_candidate_id": candidate.id,
                    "rendered_uri": rendered_uri,
                    "review": {
                        "reviewer_user_id": reviewer_user_id,
                        "reviewer_project_role": reviewer_project_role,
                        "rationale": rationale.strip(),
                        "authorization_reference": authorization_reference,
                    },
                },
                confidence=parsed_grade.value,
                status="active",
            )
            promotion = KnowledgePromotionRow(
                id=f"promotion-{uuid4()}",
                memory_candidate_id=candidate.id,
                knowledge_claim_id=claim.id,
                promoted_by=reviewer_user_id,
                authorization_reference=authorization_reference,
            )
            session.add_all([claim, promotion])
            superseded_publications: list[KnowledgePublicationRow] = []
            if supersedes_claim_id is not None:
                superseded = await session.scalar(
                    select(KnowledgeClaimRow)
                    .where(
                        KnowledgeClaimRow.id == supersedes_claim_id,
                        KnowledgeClaimRow.project_id == project_id,
                    )
                    .with_for_update()
                )
                if superseded is None:
                    raise KnowledgeLifecycleRefused("The claim selected for supersession was not found.")
                if superseded.status != "active":
                    raise KnowledgeLifecycleRefused("Only an active claim can be superseded.")
                superseded.status = "superseded"
                superseded_evidence = dict(superseded.evidence or {})
                superseded_evidence["supersession"] = {
                    "superseded_by_claim_id": claim.id,
                    "reviewer_user_id": reviewer_user_id,
                    "rationale": rationale.strip(),
                }
                superseded.evidence = superseded_evidence
                superseded_publications = list(
                    (
                        await session.execute(
                            select(KnowledgePublicationRow).where(
                                KnowledgePublicationRow.claim_id == supersedes_claim_id,
                                KnowledgePublicationRow.status == "active",
                            )
                        )
                    ).scalars()
                )
                now = datetime.now(UTC)
                for publication in superseded_publications:
                    publication.status = "superseded"
                    publication.retracted_at = now
                    publication.pointer = {
                        **dict(publication.pointer or {}),
                        "status": "superseded",
                        "superseded_by_claim_id": claim.id,
                    }
                session.add(
                    KnowledgeLinkRow(
                        id=f"knowledge-link-{uuid4()}",
                        project_id=project_id,
                        from_claim_id=claim.id,
                        to_claim_id=supersedes_claim_id,
                        relationship="supersedes",
                    )
                )
            candidate.status = "promoted"
            self._add_knowledge_event(
                session,
                project_id=project_id,
                cycle_id=candidate.cycle_id,
                candidate_id=candidate.id,
                claim_id=claim.id,
                event_type="claim.promoted",
                actor_user_id=reviewer_user_id,
                idempotency_key=idempotency_key,
                payload={
                    "grade": parsed_grade.value,
                    "rationale": rationale.strip(),
                    "rendered_uri": rendered_uri,
                    "supersedes_claim_id": supersedes_claim_id,
                    "removed_target_project_ids": [row.target_project_id for row in superseded_publications],
                },
            )
            await session.commit()
            cycle_id = candidate.cycle_id
        view = await self.knowledge_view(project_id, cycle_id=cycle_id)
        view["claim"] = next(item for item in view["claims"] if item["id"] == resolved_claim_id)
        view["superseded_claim_id"] = supersedes_claim_id
        return view

    async def publish_claim(
        self,
        *,
        claim_id: str,
        source_project_id: str,
        target_project_ids: list[str],
        publisher_user_id: str,
        publisher_project_role: str,
        rationale: str,
        authorization_reference: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        targets = list(dict.fromkeys(item.strip() for item in target_project_ids if item.strip()))
        if not targets:
            raise ValueError("Publication requires at least one selected project.")
        if source_project_id in targets:
            raise ValueError("The source project is already in claim scope.")
        if not rationale.strip():
            raise ValueError("Publication requires a rationale.")
        async with self._sf() as session:
            claim = await session.scalar(
                select(KnowledgeClaimRow)
                .where(
                    KnowledgeClaimRow.id == claim_id,
                    KnowledgeClaimRow.project_id == source_project_id,
                )
                .with_for_update()
            )
            if claim is None:
                raise KnowledgeLifecycleRefused("Claim not found.")
            if await self._knowledge_replay(
                session,
                source_project_id,
                idempotency_key,
                event_type="claim.published",
                claim_id=claim.id,
            ):
                await session.rollback()
                return await self.knowledge_view(source_project_id)
            if claim.status != "active":
                raise KnowledgeLifecycleRefused(f"Only an active claim can be published; this claim is {claim.status!r}.")
            source = await session.get(ProjectRow, source_project_id)
            rows = list(
                (
                    await session.execute(
                        select(ProjectRow).where(
                            ProjectRow.id.in_(targets),
                            ProjectRow.status == "active",
                        )
                    )
                ).scalars()
            )
            if source is None or len(rows) != len(targets):
                raise KnowledgeLifecycleRefused("Every publication target must be an active project.")
            if any(row.workspace_id != source.workspace_id for row in rows):
                raise KnowledgeLifecycleRefused("Publication targets must belong to the source workspace.")
            existing = set(
                (
                    await session.execute(
                        select(KnowledgePublicationRow.target_project_id).where(
                            KnowledgePublicationRow.claim_id == claim_id,
                            KnowledgePublicationRow.target_project_id.in_(targets),
                        )
                    )
                ).scalars()
            )
            if existing:
                raise KnowledgeLifecycleRefused("This claim already has publication history for: " + ", ".join(sorted(existing)))
            publications: list[KnowledgePublicationRow] = []
            for target in targets:
                row = KnowledgePublicationRow(
                    id=f"publication-{uuid4()}",
                    claim_id=claim.id,
                    source_project_id=source_project_id,
                    target_project_id=target,
                    status="active",
                    pointer=publication_pointer(
                        claim_id=claim.id,
                        source_project_id=source_project_id,
                        target_project_id=target,
                        statement=claim.statement,
                        grade=claim.confidence,
                    ),
                    published_by=publisher_user_id,
                    publisher_project_role=publisher_project_role,
                    rationale=rationale.strip(),
                    authorization_reference=authorization_reference,
                )
                session.add(row)
                publications.append(row)
            self._add_knowledge_event(
                session,
                project_id=source_project_id,
                claim_id=claim.id,
                event_type="claim.published",
                actor_user_id=publisher_user_id,
                idempotency_key=idempotency_key,
                payload={
                    "target_project_ids": targets,
                    "publication_ids": [row.id for row in publications],
                    "rationale": rationale.strip(),
                },
            )
            await session.commit()
        return await self.knowledge_view(source_project_id)

    async def retract_claim(
        self,
        *,
        claim_id: str,
        project_id: str,
        reviewer_user_id: str,
        reviewer_project_role: str,
        rationale: str,
        authorization_reference: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not rationale.strip():
            raise ValueError("Retraction requires a rationale.")
        async with self._sf() as session:
            claim = await session.scalar(
                select(KnowledgeClaimRow)
                .where(
                    KnowledgeClaimRow.id == claim_id,
                    KnowledgeClaimRow.project_id == project_id,
                )
                .with_for_update()
            )
            if claim is None:
                raise KnowledgeLifecycleRefused("Claim not found.")
            if await self._knowledge_replay(
                session,
                project_id,
                idempotency_key,
                event_type="claim.retracted",
                claim_id=claim.id,
            ):
                await session.rollback()
                view = await self.knowledge_view(project_id)
                view["claim"] = next(item for item in view["claims"] if item["id"] == claim_id)
                return view
            if claim.status != "active":
                raise KnowledgeLifecycleRefused(f"Only an active claim can be retracted; this claim is {claim.status!r}.")
            claim.status = "retracted"
            publications = list(
                (
                    await session.execute(
                        select(KnowledgePublicationRow).where(
                            KnowledgePublicationRow.claim_id == claim.id,
                            KnowledgePublicationRow.status == "active",
                        )
                    )
                ).scalars()
            )
            now = datetime.now(UTC)
            for publication in publications:
                publication.status = "retracted"
                publication.retracted_at = now
                publication.pointer = {
                    **dict(publication.pointer or {}),
                    "status": "retracted",
                }
            evidence = dict(claim.evidence or {})
            evidence["retraction"] = {
                "reviewer_user_id": reviewer_user_id,
                "reviewer_project_role": reviewer_project_role,
                "rationale": rationale.strip(),
                "authorization_reference": authorization_reference,
                "retracted_at": now.isoformat(),
                "removed_target_project_ids": [row.target_project_id for row in publications],
            }
            claim.evidence = evidence
            self._add_knowledge_event(
                session,
                project_id=project_id,
                claim_id=claim.id,
                event_type="claim.retracted",
                actor_user_id=reviewer_user_id,
                idempotency_key=idempotency_key,
                payload=evidence["retraction"],
            )
            await session.commit()
        view = await self.knowledge_view(project_id)
        view["claim"] = next(item for item in view["claims"] if item["id"] == claim_id)
        return view
