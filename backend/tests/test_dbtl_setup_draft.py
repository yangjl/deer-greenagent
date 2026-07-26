"""The LLM-drafted cycle setup: prompt construction and response parsing.

Kept pure so the integrity rules are tested directly rather than inferred from a
live model call. The rule that matters most: a value the request does not
support must be reported as an *assumption*, never as something the scientist
said. A drafted form that silently mixes the two would let a rubber-stamped
confirmation put invented specifics on a durable research record.
"""

from deerflow.dbtl.setup_draft import (
    SETUP_DRAFT_MAX_VALUE_CHARS,
    build_draft_prompt,
    empty_draft,
    parse_draft_response,
)

FIELDS = (
    "research objective",
    "target trait",
    "season range",
    "validation expectation",
    "population scope",
)


class TestPrompt:
    def test_asks_only_for_the_fields_the_proposal_is_missing(self):
        prompt = build_draft_prompt(
            request_text="design a genomic selection project for maize",
            project_name="test1",
            fields=("target trait", "season range"),
        )
        assert "target trait" in prompt
        assert "season range" in prompt
        assert "validation expectation" not in prompt

    def test_carries_the_request_and_project_for_grounding(self):
        prompt = build_draft_prompt(
            request_text="design a genomic selection project for maize",
            project_name="test1",
            fields=FIELDS,
        )
        assert "design a genomic selection project for maize" in prompt
        assert "test1" in prompt

    def test_forbids_inventing_dataset_specifics(self):
        # The failure this guards against: a draft reading "1,204 hybrids across
        # 14 environments" when the scientist never said any such thing.
        prompt = build_draft_prompt(
            request_text="design a genomic selection project",
            project_name="test1",
            fields=FIELDS,
        )
        lowered = prompt.lower()
        assert "do not invent" in lowered
        assert "grounded" in lowered

    def test_requires_a_grounded_flag_per_field(self):
        prompt = build_draft_prompt(request_text="x", project_name="p", fields=("target trait",))
        assert "grounded" in prompt.lower()


class TestParsing:
    def test_reads_values_and_title(self):
        raw = """{"title": "Genomic selection in maize",
          "fields": {"target trait": {"value": "grain yield", "grounded": false}}}"""
        draft = parse_draft_response(raw, fields=("target trait",))
        assert draft.title == "Genomic selection in maize"
        assert draft.fields["target trait"] == "grain yield"

    def test_marks_ungrounded_values_as_assumptions(self):
        raw = """{"title": "t", "fields": {
          "target trait": {"value": "grain yield", "grounded": false},
          "research objective": {"value": "compare GS strategies", "grounded": true}}}"""
        draft = parse_draft_response(raw, fields=("target trait", "research objective"))
        assert "target trait" in draft.assumed
        assert "research objective" not in draft.assumed

    def test_treats_a_missing_grounded_flag_as_an_assumption(self):
        # Defaulting to "the scientist said this" would be the unsafe default.
        raw = '{"title": "t", "fields": {"target trait": {"value": "grain yield"}}}'
        draft = parse_draft_response(raw, fields=("target trait",))
        assert "target trait" in draft.assumed

    def test_accepts_a_bare_string_value_as_an_assumption(self):
        raw = '{"title": "t", "fields": {"target trait": "grain yield"}}'
        draft = parse_draft_response(raw, fields=("target trait",))
        assert draft.fields["target trait"] == "grain yield"
        assert "target trait" in draft.assumed

    def test_tolerates_fenced_json(self):
        raw = '```json\n{"title": "t", "fields": {}}\n```'
        assert parse_draft_response(raw, fields=FIELDS).title == "t"

    def test_drops_fields_that_were_not_requested(self):
        raw = """{"title": "t", "fields": {
          "target trait": {"value": "yield", "grounded": true},
          "secret field": {"value": "nope", "grounded": true}}}"""
        draft = parse_draft_response(raw, fields=("target trait",))
        assert set(draft.fields) == {"target trait"}

    def test_omits_empty_values_rather_than_filling_blanks(self):
        raw = """{"title": "", "fields": {
          "target trait": {"value": "   ", "grounded": true}}}"""
        draft = parse_draft_response(raw, fields=("target trait",))
        assert "target trait" not in draft.fields
        assert draft.title == ""

    def test_caps_runaway_values(self):
        long_value = "y" * (SETUP_DRAFT_MAX_VALUE_CHARS * 3)
        raw = '{"title": "t", "fields": {"target trait": {"value": "' + long_value + '", "grounded": true}}}'
        draft = parse_draft_response(raw, fields=("target trait",))
        assert len(draft.fields["target trait"]) <= SETUP_DRAFT_MAX_VALUE_CHARS

    def test_malformed_output_degrades_to_an_empty_draft(self):
        # A model outage or a non-JSON reply must leave the human with the blank
        # form they had before, never block the setup step.
        for raw in ("not json at all", "", "[]", '{"fields": 3}'):
            draft = parse_draft_response(raw, fields=FIELDS)
            assert draft.fields == {}
            assert draft.title == ""

    def test_empty_draft_has_nothing_assumed(self):
        draft = empty_draft()
        assert draft.title == ""
        assert draft.fields == {}
        assert draft.assumed == frozenset()
