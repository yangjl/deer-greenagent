"""The Build write-up: what it may claim, and what the deck may show.

A Build package used to answer "what files exist". These tests pin the two rules
that make it answer "what did we get" instead, and the two that keep it honest:

* a summarizer may **only** cite evidence the server verified, and
* choosing a few figures for the deck never removes one from the record.

Plus the renderer's own boundary — it has no sentence of its own, so a figure it
cannot inline is *named* rather than dropped, and a build with none says so
rather than rendering an empty gallery.
"""

from __future__ import annotations

import base64
import json

from deerflow.dbtl.build_deck import MAX_FIGURE_BYTES, embed_figures, render_build_deck
from deerflow.dbtl.build_execution import BuildExecutionBundle, BuildFigure, KeyOutcome, parse_execution_bundle
from deerflow.dbtl.build_summary import MAX_SUMMARY_FIGURES, BuildReviewPackage, SelectedFigure, parse_build_summary, render_summary_markdown

PUBLISHED = {
    "figs/roc.png": "/mnt/user-data/outputs/dbtl/c1/build/artifacts/a1/abc-roc.png",
    "figs/resid.png": "/mnt/user-data/outputs/dbtl/c1/build/artifacts/a1/def-resid.png",
}
ROC = PUBLISHED["figs/roc.png"]
RESID = PUBLISHED["figs/resid.png"]


def _bundle(**overrides) -> BuildExecutionBundle:
    base = {
        "outputs": (ROC, RESID),
        "figures": (
            BuildFigure(path=ROC, caption="ROC curve", shows="Discrimination on the held-out site."),
            BuildFigure(path=RESID, caption="Residuals", shows="Whether error grows with predicted yield."),
        ),
        "key_outcomes": (KeyOutcome(name="Held-out accuracy", value="0.62", unit="r"),),
        "rerun_procedure": "uv run python fit.py --seed 7",
    }
    return BuildExecutionBundle(**{**base, **overrides})


class TestDeclarationsAreVerifiedNotTrusted:
    def test_a_figure_the_server_never_published_does_not_exist(self) -> None:
        bundle = parse_execution_bundle(
            [{"figures": [{"path": "figs/roc.png", "caption": "ROC"}, {"path": "figs/imaginary.png", "caption": "Nope"}]}],
            published=PUBLISHED,
        )

        assert [figure.path for figure in bundle.figures] == [ROC]
        # Kept rather than dropped: "the worker said it made a figure and there
        # is none" is something a reviewer should be able to see.
        assert bundle.unverified == ("figs/imaginary.png",)

    def test_a_declaration_may_name_the_published_uri_directly(self) -> None:
        bundle = parse_execution_bundle([{"figures": [{"path": ROC, "caption": "ROC"}]}], published=PUBLISHED)

        assert [figure.path for figure in bundle.figures] == [ROC]

    def test_a_malformed_declaration_costs_that_figure_not_the_build(self) -> None:
        """The science is already on disk and hash-bound by this point."""
        bundle = parse_execution_bundle(
            [{"figures": ["not-an-object", {"caption": "no path"}, {"path": "figs/roc.png"}], "key_outcomes": [{"name": "", "value": "1"}, {"name": "r", "value": 0.62}]}],
            published=PUBLISHED,
        )

        assert [figure.path for figure in bundle.figures] == [ROC]
        assert [(outcome.name, outcome.value) for outcome in bundle.key_outcomes] == [("r", "0.62")]

    def test_only_image_formats_are_marked_embeddable(self) -> None:
        assert BuildFigure(path="a/b.png").is_embeddable
        assert not BuildFigure(path="a/b.pdf").is_embeddable

    def test_the_rerun_procedure_is_carried_from_provenance(self) -> None:
        bundle = parse_execution_bundle([{"provenance": {"recorded_rerun_procedure": "python fit.py"}}], published=PUBLISHED)

        assert bundle.rerun_procedure == "python fit.py"


class TestTheSummarizerCannotCiteWhatDoesNotExist:
    def test_a_cited_figure_must_be_one_the_server_verified(self) -> None:
        parsed = parse_build_summary(
            json.dumps({"headline": "Fitted.", "figures": [{"path": "figs/invented.png", "reading": "Looks great."}]}),
            bundle=_bundle(),
        )

        assert not parsed.ok
        assert "did not verify" in parsed.refusal

    def test_a_valid_summary_keeps_every_declared_figure_in_the_record(self) -> None:
        parsed = parse_build_summary(
            json.dumps({"headline": "Fitted.", "figures": [{"path": ROC, "reading": "Separation is clear."}]}),
            bundle=_bundle(),
        )

        assert parsed.ok
        package = parsed.package
        assert [item.figure.path for item in package.selected_figures] == [ROC]
        assert [figure.path for figure in package.all_figures] == [ROC, RESID]
        assert package.withheld_figure_count == 1

    def test_selecting_no_figures_is_a_valid_summary(self) -> None:
        """Some builds legitimately produce none, and some produce none worth
        leading with. Neither is a contract failure."""
        parsed = parse_build_summary(json.dumps({"headline": "Fitted; nothing to plot."}), bundle=_bundle(figures=()))

        assert parsed.ok
        assert parsed.package.selected_figures == ()

    def test_selection_is_capped(self) -> None:
        figures = tuple(BuildFigure(path=f"/pub/f{index}.png") for index in range(MAX_SUMMARY_FIGURES + 4))
        parsed = parse_build_summary(
            json.dumps({"headline": "Fitted.", "figures": [{"path": figure.path} for figure in figures]}),
            bundle=_bundle(figures=figures),
        )

        assert len(parsed.package.selected_figures) == MAX_SUMMARY_FIGURES
        assert len(parsed.package.all_figures) == len(figures)

    def test_a_fenced_answer_is_repaired_rather_than_failing_a_build(self) -> None:
        parsed = parse_build_summary('```json\n{"headline": "Fitted."}\n```', bundle=_bundle())

        assert parsed.ok
        assert parsed.repaired == ("unwrapped_code_fence",)

    def test_unreadable_output_fails_only_this_step(self) -> None:
        parsed = parse_build_summary("I summarized it, trust me.", bundle=_bundle())

        assert not parsed.ok
        assert not parsed.needs_input
        assert "not valid JSON" in parsed.refusal

    def test_a_summary_with_no_headline_is_refused(self) -> None:
        assert "did not state" in parse_build_summary(json.dumps({"figures": []}), bundle=_bundle()).refusal

    def test_the_summarizer_may_ask_for_a_human_decision(self) -> None:
        parsed = parse_build_summary(
            json.dumps({"status": "needs_input", "clarification_question": "Should the outlier site be excluded before reporting?"}),
            bundle=_bundle(),
        )

        assert parsed.needs_input
        assert not parsed.ok
        assert parsed.clarification_question.startswith("Should the outlier site")

    def test_asking_for_input_without_a_question_is_refused(self) -> None:
        assert "without stating a question" in parse_build_summary(json.dumps({"status": "needs_input"}), bundle=_bundle()).refusal

    def test_an_outcome_citing_an_unknown_figure_keeps_the_number(self) -> None:
        """The value stands on its own; the citation is a convenience."""
        parsed = parse_build_summary(
            json.dumps({"headline": "Fitted.", "key_outcomes": [{"name": "r", "value": 0.62, "unit": "corr", "figure": "/nope.png"}]}),
            bundle=_bundle(),
        )

        (outcome,) = parsed.package.key_outcomes
        assert (outcome.name, outcome.value, outcome.unit, outcome.figure) == ("r", "0.62", "corr", "")


class TestTheReviewedDocumentLeadsWithTheResult:
    def test_deviations_are_printed_before_the_rerun_notes(self) -> None:
        markdown = render_summary_markdown(
            BuildReviewPackage(headline="Fitted.", deviations=("Dropped site 4.",), rerun_procedure="python fit.py"),
            title="Build review",
        )

        assert markdown.index("Deviations and limitations") < markdown.index("How to re-run it")

    def test_a_build_with_no_figures_says_so(self) -> None:
        markdown = render_summary_markdown(BuildReviewPackage(headline="Fitted."), title="Build review")

        assert "This build produced no figures." in markdown

    def test_withheld_figures_are_announced_and_listed(self) -> None:
        bundle = _bundle()
        package = parse_build_summary(json.dumps({"headline": "Fitted.", "figures": [{"path": ROC}]}), bundle=bundle).package

        markdown = render_summary_markdown(package, title="Build review")

        assert "1 further figure(s)" in markdown
        assert RESID in markdown


def _png(size: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"0" * max(0, size - 8)


class TestTheDeckEmbedsWhatItCanAndNamesWhatItCannot:
    def test_a_readable_image_is_inlined(self) -> None:
        (embedded,) = embed_figures((SelectedFigure(figure=BuildFigure(path=ROC)),), read=lambda _path: (_png(64), "image/png"))

        assert embedded.embedded
        assert embedded.data_uri.startswith("data:image/png;base64,")
        assert base64.b64decode(embedded.data_uri.split(",", 1)[1]) == _png(64)

    def test_an_unembeddable_format_is_named_rather_than_dropped(self) -> None:
        (embedded,) = embed_figures((SelectedFigure(figure=BuildFigure(path="/pub/report.pdf")),), read=lambda _path: (b"%PDF", "application/pdf"))

        assert not embedded.embedded
        assert "cannot be embedded" in embedded.skipped_reason

    def test_an_oversized_figure_is_named_rather_than_dropped(self) -> None:
        (embedded,) = embed_figures((SelectedFigure(figure=BuildFigure(path=ROC)),), read=lambda _path: (_png(MAX_FIGURE_BYTES + 1), "image/png"))

        assert not embedded.embedded
        assert "too large to embed" in embedded.skipped_reason

    def test_the_whole_deck_is_bounded_too(self) -> None:
        """One figure under the per-figure cap can still be the one that makes a
        deck unopenable, so the running total is enforced as well."""
        figures = tuple(SelectedFigure(figure=BuildFigure(path=f"/pub/f{index}.png")) for index in range(3))

        embedded = embed_figures(figures, read=lambda _path: (_png(400), "image/png"), max_figure_bytes=1000, max_total_bytes=900)

        assert [item.embedded for item in embedded] == [True, True, False]
        assert "embedded-figure limit" in embedded[2].skipped_reason

    def test_an_unreadable_figure_does_not_raise(self) -> None:
        def _boom(_path: str):
            raise OSError("gone")

        (embedded,) = embed_figures((SelectedFigure(figure=BuildFigure(path=ROC)),), read=_boom)

        assert "could not be read" in embedded.skipped_reason


class TestTheDeckHasNoSentenceOfItsOwn:
    def _render(self, package: BuildReviewPackage) -> str:
        return render_build_deck(package, title="Cycle 01 — Build", package_path="/mnt/user-data/outputs/build.md", read_figure=lambda _p: (_png(64), "image/png"))

    def test_slide_order_puts_caveats_before_the_rerun_notes(self) -> None:
        html = self._render(BuildReviewPackage(headline="Fitted.", deviations=("Dropped site 4.",), rerun_procedure="python fit.py"))

        assert html.index("Key outcomes") < html.index("Deviations and limitations") < html.index("How to re-run it")

    def test_it_is_self_contained(self) -> None:
        html = self._render(
            BuildReviewPackage(
                headline="Fitted.",
                selected_figures=(SelectedFigure(figure=BuildFigure(path=ROC, caption="ROC"), reading="Separation is clear."),),
                all_figures=(BuildFigure(path=ROC),),
            )
        )

        assert "data:image/png;base64," in html
        assert "http://" not in html and "https://" not in html
        assert "<script" in html and 'src="http' not in html

    def test_a_build_with_no_figures_renders_a_statement_not_an_empty_gallery(self) -> None:
        html = self._render(BuildReviewPackage(headline="Fitted."))

        assert "This build produced no figures to show." in html

    def test_package_content_is_escaped(self) -> None:
        html = self._render(BuildReviewPackage(headline="<script>alert(1)</script>"))

        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html

    def test_withheld_figures_are_listed_on_their_own_slide(self) -> None:
        package = BuildReviewPackage(
            headline="Fitted.",
            selected_figures=(SelectedFigure(figure=BuildFigure(path=ROC)),),
            all_figures=(BuildFigure(path=ROC), BuildFigure(path=RESID)),
        )

        html = self._render(package)

        assert "Other figures produced" in html
        assert RESID in html
