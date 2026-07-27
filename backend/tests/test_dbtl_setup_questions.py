"""The model writes the setup questions; this module decides what may be shown.

A drafted question is cheap to get wrong in an expensive way: the answers
become a durable research record, so a recommendation the model *invented* must
never reach the card looking like something the scientist *stated*. These tests
pin that boundary, and the fallback that keeps a model outage from blocking
setup entirely.
"""

from __future__ import annotations

from deerflow.dbtl.setup_questions import (
    MAX_SETUP_QUESTIONS,
    SetupQuestion,
    build_questions_prompt,
    fallback_questions,
    parse_questions_response,
    render_questions,
)

_FIELDS = ("target trait", "season range", "validation expectation", "population scope")

_GOOD_REPLY = """{"questions": [
  {"id": "trait", "question": "Which trait should the simulation produce?",
   "why": "Design cannot pin an outcome without it.",
   "recommendation": "plant height", "grounded": false},
  {"id": "scale", "question": "How many individuals and markers?",
   "recommendation": "100 inbreds x 10 SNP markers", "grounded": true}
]}"""


def test_the_prompt_carries_the_request_and_the_known_gaps() -> None:
    prompt = build_questions_prompt(
        request_text="start a maize phenotype and genotype simulation work",
        project_name="test3",
        missing_fields=_FIELDS,
    )
    assert "maize phenotype and genotype simulation" in prompt
    assert "test3" in prompt
    # The deterministic gaps are a hint, not a script: the model may ask
    # better questions, but it should not be blind to what the rules noticed.
    assert "target trait" in prompt
    assert "recommendation" in prompt.lower()


def test_a_well_formed_reply_becomes_questions_with_provenance() -> None:
    questions = parse_questions_response(_GOOD_REPLY, missing_fields=_FIELDS)

    assert [q.id for q in questions] == ["trait", "scale"]
    assert questions[0].question.startswith("Which trait")
    assert questions[0].recommendation == "plant height"
    # An unsupported recommendation is an assumption, and says so.
    assert questions[0].grounded is False
    assert questions[1].grounded is True


def test_a_recommendation_without_an_explicit_grounded_flag_is_an_assumption() -> None:
    """The unsafe default — a silent model read as authoritative — is unreachable."""
    questions = parse_questions_response(
        '{"questions": [{"id": "a", "question": "Which trait?", "recommendation": "yield"}]}',
        missing_fields=_FIELDS,
    )
    assert questions[0].grounded is False


def test_a_malformed_reply_degrades_to_the_deterministic_questions() -> None:
    """A model outage must not block cycle setup."""
    for raw in ("", "not json", "{}", '{"questions": "nope"}', '{"questions": []}'):
        questions = parse_questions_response(raw, missing_fields=_FIELDS)
        assert questions == fallback_questions(_FIELDS), f"bad degrade for {raw!r}"


def test_the_fallback_asks_the_deterministic_gaps_as_real_questions() -> None:
    questions = fallback_questions(_FIELDS)
    assert len(questions) == len(_FIELDS)
    assert all(q.question.endswith("?") for q in questions), "a bullet is not a question"
    assert all(q.recommendation == "" for q in questions), "the fallback may not invent an answer"


def test_a_question_with_no_text_is_dropped_rather_than_shown_empty() -> None:
    questions = parse_questions_response(
        '{"questions": [{"id": "a", "question": "  "}, {"id": "b", "question": "Which trait?"}]}',
        missing_fields=_FIELDS,
    )
    assert [q.id for q in questions] == ["b"]


def test_the_question_list_is_bounded() -> None:
    many = ", ".join(f'{{"id": "q{i}", "question": "Question {i}?"}}' for i in range(20))
    questions = parse_questions_response(f'{{"questions": [{many}]}}', missing_fields=_FIELDS)
    assert len(questions) == MAX_SETUP_QUESTIONS


def test_fenced_json_is_accepted() -> None:
    questions = parse_questions_response(f"```json\n{_GOOD_REPLY}\n```", missing_fields=_FIELDS)
    assert len(questions) == 2


def test_the_rendered_card_separates_a_recommendation_from_a_statement() -> None:
    """A scientist skimming the card must see which answers they did not give."""
    rendered = render_questions(parse_questions_response(_GOOD_REPLY, missing_fields=_FIELDS))

    assert "Which trait should the simulation produce?" in rendered
    assert "plant height" in rendered
    # The assumed one is labelled; the grounded one is not called a suggestion.
    trait_block, scale_block = rendered.split("2.")
    assert "suggested" in trait_block.lower()
    assert "from your request" in scale_block.lower()


def test_rendering_invites_acceptance_rather_than_transcription() -> None:
    """Minimum human input: the point of a recommendation is not retyping it."""
    rendered = render_questions(parse_questions_response(_GOOD_REPLY, missing_fields=_FIELDS))
    assert "accept" in rendered.lower()


def test_rendering_a_question_without_a_recommendation_stays_clean() -> None:
    rendered = render_questions(fallback_questions(("target trait",)))
    assert "suggested" not in rendered.lower()
    assert "target trait" in rendered.lower()


def test_questions_are_frozen_values() -> None:
    question = SetupQuestion(id="a", question="Which trait?", why="", recommendation="yield", grounded=False)
    assert question == SetupQuestion(id="a", question="Which trait?", why="", recommendation="yield", grounded=False)
