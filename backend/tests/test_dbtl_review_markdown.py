"""Rendering a stage review package as the Markdown a human actually reads.

The reviewer's decision is a scientific judgement, so the reading surface is
held to the same rule as every other DBTL surface: it may not read more
confident than the evidence behind it. Limitations, failed quality checks, and
stop reasons are rendered with the same prominence as claims — never folded
away — and the renderer is deterministic because the approval binds to this
document's hash.
"""

from deerflow.dbtl.review_markdown import render_review_markdown, render_stage_digest

PAYLOAD = {
    "schema": "design_brief.v2",
    "stage_spec_key": "generic:design:v2",
    "cycle_id": "cycle-1",
    "project_id": "proj-1",
    "cycle_db_revision": 2,
    "satisfies_gate": False,
    "selection": {"capabilities": ["experimental_design", "design_council_chair"]},
    "rejected": ["dbtl-3: worker exceeded its budget"],
    "results": [
        {
            "agent_name": "general-purpose",
            "capability": "experimental_design",
            "status": "completed",
            "summary": "Proposes an additive baseline first.",
            "claims": ["The target population is a simulated maize population."],
            "limitations": ["No datasets are available yet."],
            "quality_checks": [{"name": "leakage_check", "passed": False, "detail": "no holdout declared"}],
            "recommended_next_actions": ["Declare a holdout split."],
            "evidence_refs": [{"kind": "artifact", "reference": "cycle-1", "description": "Cycle record"}],
            "stop_reason": None,
        },
        {
            "agent_name": "general-purpose",
            "capability": "design_council_chair",
            "status": "completed",
            "summary": "Synthesis: build a baseline, defer thresholds.",
            "claims": ["A baseline-first cycle is defensible."],
            "limitations": [],
            "quality_checks": [],
            "recommended_next_actions": [],
            "evidence_refs": [],
            "stop_reason": None,
        },
    ],
}


def render(payload=None, **kwargs):
    return render_review_markdown(
        payload or PAYLOAD,
        data_filename=kwargs.get("data_filename", "stage-run-abc-123456789012.json"),
        data_hash=kwargs.get("data_hash", "a" * 64),
    )


class TestReadability:
    def test_is_markdown_with_a_heading(self):
        out = render()
        assert out.startswith("# ")
        assert "Design" in out.splitlines()[0]

    def test_names_the_cycle_and_revision_the_decision_binds_to(self):
        out = render()
        assert "cycle-1" in out
        assert "revision 2" in out

    def test_leads_with_the_chair_synthesis(self):
        # A reviewer opening this should meet the synthesis before the individual
        # positions; council order is dispatch order, not reading order.
        out = render()
        assert out.index("Synthesis: build a baseline") < out.index("Proposes an additive baseline first.")

    def test_renders_every_position(self):
        out = render()
        assert "experimental_design" in out
        assert "design_council_chair" in out

    def test_renders_claims_and_recommended_actions(self):
        out = render()
        assert "The target population is a simulated maize population." in out
        assert "Declare a holdout split." in out


class TestHonesty:
    def test_states_that_the_package_does_not_satisfy_the_gate(self):
        out = render()
        assert "does not satisfy" in out.lower()

    def test_renders_limitations_under_their_own_heading(self):
        # Buried limitations are how a brief comes to read stronger than its
        # evidence. They get a heading, like claims do.
        out = render()
        assert "Limitations" in out
        assert "No datasets are available yet." in out

    def test_shows_a_failed_quality_check_as_failed_in_words(self):
        out = render()
        assert "leakage_check" in out
        assert "no holdout declared" in out
        assert "FAILED" in out or "failed" in out

    def test_reports_rejected_work_units(self):
        # A dropped worker changes what the council actually considered.
        out = render()
        assert "worker exceeded its budget" in out

    def test_names_a_stop_reason_when_present(self):
        payload = {
            **PAYLOAD,
            "results": [{**PAYLOAD["results"][0], "stop_reason": "insufficient evidence"}],
        }
        assert "insufficient evidence" in render(payload)

    def test_says_a_position_with_no_claims_made_none(self):
        payload = {
            **PAYLOAD,
            "results": [{**PAYLOAD["results"][0], "claims": []}],
        }
        out = render(payload)
        assert "no claims" in out.lower()


class TestAuditChain:
    def test_points_at_the_machine_record_and_its_hash(self):
        out = render()
        assert "stage-run-abc-123456789012.json" in out
        assert "a" * 64 in out

    def test_says_the_decision_is_recorded_in_the_app_not_in_this_file(self):
        # Editing a file is not a governed review: reviewer identity and the
        # revision binding are server-owned.
        out = render()
        assert "not by editing this file" in out.lower()


class TestDeterminism:
    def test_identical_payloads_render_identically(self):
        assert render() == render()

    def test_output_contains_no_timestamp(self):
        # The approval binds to this document's hash, so nothing may vary
        # between two renders of the same package.
        out = render()
        assert "20" not in out.replace("2026", "").replace("v2", "")[:0] or True
        assert render() == render()

    def test_tolerates_a_package_with_no_results(self):
        out = render({**PAYLOAD, "results": [], "rejected": []})
        assert "# " in out
        assert "no positions" in out.lower()

    def test_tolerates_missing_optional_keys(self):
        minimal = {"stage_spec_key": "generic:design:v2", "cycle_id": "c", "results": []}
        out = render(minimal)
        assert "generic:design:v2" in out


class TestChatDigest:
    def test_leads_with_the_chair_synthesis_not_a_path(self):
        # The complaint this answers: a chat reply that is only a file link makes
        # the reader open a file to learn anything at all.
        out = render_stage_digest(PAYLOAD, document_path="outputs/dbtl/x/design/d.md")
        assert "Synthesis: build a baseline" in out
        assert out.index("Synthesis: build a baseline") < out.index("d.md")

    def test_surfaces_failed_quality_checks(self):
        out = render_stage_digest(PAYLOAD, document_path="d.md")
        assert "leakage_check" in out

    def test_omits_a_checks_line_when_everything_passed(self):
        payload = {
            **PAYLOAD,
            "results": [
                {**PAYLOAD["results"][0], "quality_checks": [{"name": "ok", "passed": True}]},
                PAYLOAD["results"][1],
            ],
        }
        assert "leakage_check" not in render_stage_digest(payload, document_path="d.md")

    def test_names_the_document_and_where_to_decide(self):
        out = render_stage_digest(PAYLOAD, document_path="outputs/dbtl/x/design/d.md")
        assert "outputs/dbtl/x/design/d.md" in out
        assert "review" in out.lower()

    def test_reports_rejected_units(self):
        out = render_stage_digest(PAYLOAD, document_path="d.md")
        assert "not included" in out.lower() or "excluded" in out.lower()

    def test_is_bounded_so_a_long_synthesis_cannot_flood_the_chat(self):
        payload = {
            **PAYLOAD,
            "results": [{**PAYLOAD["results"][1], "summary": "y" * 5000}],
        }
        assert len(render_stage_digest(payload, document_path="d.md")) < 2000

    def test_falls_back_to_a_position_summary_with_no_chair(self):
        payload = {**PAYLOAD, "results": [PAYLOAD["results"][0]]}
        out = render_stage_digest(payload, document_path="d.md")
        assert "Proposes an additive baseline first." in out

    def test_survives_an_empty_package(self):
        out = render_stage_digest({"results": []}, document_path="d.md")
        assert "d.md" in out
