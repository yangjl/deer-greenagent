"""The model writes the setup questions; this module decides what may be shown.

A drafted question is cheap to get wrong in an expensive way: the answers
become a durable research record, so a recommendation the model *invented* must
never reach the card looking like something the scientist *stated*. These tests
pin that boundary, and the fallback that keeps a model outage from blocking
setup entirely.
"""

from __future__ import annotations

from deerflow.dbtl.setup_questions import (
    MAX_OPTIONS_PER_QUESTION,
    MAX_SETUP_QUESTIONS,
    SetupOption,
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
   "options": [
     {"id": "height", "label": "Plant height", "description": "Simple, highly heritable", "recommended": true},
     {"id": "yield", "label": "Grain yield", "description": "The breeding target, lower heritability"}
   ]},
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
    assert "options" in prompt.lower()


def test_the_prompt_asks_for_selectable_options_not_prose() -> None:
    """The card is answered by picking, so the model must supply things to pick."""
    prompt = build_questions_prompt(request_text="x", project_name="p", missing_fields=())
    assert "label" in prompt.lower()
    assert "description" in prompt.lower()
    assert "recommended" in prompt.lower()


def test_options_are_parsed_with_their_descriptions_and_one_recommendation() -> None:
    questions = parse_questions_response(_GOOD_REPLY, missing_fields=_FIELDS)

    trait = questions[0]
    assert [option.id for option in trait.options] == ["height", "yield"]
    assert trait.options[0].label == "Plant height"
    assert trait.options[0].description == "Simple, highly heritable"
    assert trait.recommended_option_id == "height"


def test_a_question_may_still_arrive_without_options() -> None:
    """Not everything is a menu; a free answer must remain expressible."""
    scale = parse_questions_response(_GOOD_REPLY, missing_fields=_FIELDS)[1]
    assert scale.options == ()
    assert scale.recommendation == "100 inbreds x 10 SNP markers"
    assert scale.grounded is True


def test_only_one_option_can_be_recommended() -> None:
    """Two recommendations is no recommendation; the first wins."""
    questions = parse_questions_response(
        '{"questions": [{"id": "a", "question": "Which?", "options": [{"id": "x", "label": "X", "recommended": true},{"id": "y", "label": "Y", "recommended": true}]}]}',
        missing_fields=_FIELDS,
    )
    assert questions[0].recommended_option_id == "x"


def test_a_recommendation_without_an_explicit_grounded_flag_is_an_assumption() -> None:
    """The unsafe default — a silent model read as authoritative — is unreachable."""
    questions = parse_questions_response(
        '{"questions": [{"id": "a", "question": "Which trait?", "recommendation": "yield"}]}',
        missing_fields=_FIELDS,
    )
    assert questions[0].grounded is False


def test_options_are_bounded_and_malformed_ones_are_dropped() -> None:
    many = ", ".join(f'{{"id": "o{i}", "label": "Option {i}"}}' for i in range(12))
    questions = parse_questions_response(
        f'{{"questions": [{{"id": "a", "question": "Which?", "options": [{many}, "junk", {{"label": ""}}]}}]}}',
        missing_fields=_FIELDS,
    )
    assert len(questions[0].options) == MAX_OPTIONS_PER_QUESTION


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
    assert all(q.options == () for q in questions), "nor may it invent choices"


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


def test_the_rendered_text_separates_a_recommendation_from_a_statement() -> None:
    """Clients that cannot render the wizard still get a readable card."""
    rendered = render_questions(parse_questions_response(_GOOD_REPLY, missing_fields=_FIELDS))

    assert "Which trait should the simulation produce?" in rendered
    assert "Plant height" in rendered
    trait_block, scale_block = rendered.split("2.")
    assert "suggested" in trait_block.lower()
    assert "from your request" in scale_block.lower()


def test_rendering_a_question_without_a_recommendation_stays_clean() -> None:
    rendered = render_questions(fallback_questions(("target trait",)))
    assert "suggested" not in rendered.lower()
    assert "target trait" in rendered.lower()


def test_questions_are_frozen_values() -> None:
    option = SetupOption(id="a", label="A", description="d")
    question = SetupQuestion(id="a", question="Which trait?", options=(option,))
    assert question == SetupQuestion(id="a", question="Which trait?", options=(option,))
