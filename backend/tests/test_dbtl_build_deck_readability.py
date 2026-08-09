"""The Build deck is read by a person deciding whether to approve.

Three faults, all observed in real decks from the manual profile, all of them
the renderer's rather than the worker's:

* every figure was stamped with its full governed URI — six nested directories
  and a content hash, under a picture the reader is already looking at;
* numbers appeared at raw float repr, `0.0615427182312 dimensionless
  correlation`, implying a precision the estimate does not have;
* sections with nothing in them still cost a slide — "This build reported no
  numeric outcomes", "The build did not record a rerun procedure".

None of that needs a model to fix, so none of it is left to one. The machine
record keeps the full paths, the full precision, and the empty lists.
"""

from __future__ import annotations

from deerflow.dbtl.build_deck import readable_artifact_name, readable_number, render_build_deck
from deerflow.dbtl.build_execution import BuildFigure, KeyOutcome
from deerflow.dbtl.build_summary import BuildReviewPackage, SelectedFigure

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
GOVERNED = "/mnt/user-data/outputs/dbtl/how-large-a-training-set-4ee2a915/build/artifacts/dbtl-09244aa4d907/b15e08209a09/222d948286b3bd2b-accuracy_vs_training_size.png"


def _package(**overrides) -> BuildReviewPackage:
    figure = BuildFigure(path=GOVERNED, caption="Held-out accuracy by training-set size", content_hash="a" * 64)
    base = {
        "headline": "A monotonic genomic-prediction learning curve through 600 training lines.",
        "key_outcomes": (KeyOutcome(name="Pearson accuracy at n=600", value="0.275944934835", unit="correlation"),),
        "selected_figures": (SelectedFigure(figure=figure, reading="Accuracy rises across all four training sizes."),),
        "all_figures": (figure,),
    }
    base.update(overrides)
    return BuildReviewPackage(**base)


def _render(package: BuildReviewPackage) -> str:
    return render_build_deck(package, title="Sorghum learning curve — Build", subtitle="Revision 4", package_path="outputs/dbtl/x/build/build-review-rev4.md", read_figure=lambda _p: (PNG, "image/png"))


class TestNumbersAreRoundedForReading:
    def test_a_raw_float_is_cut_to_a_readable_precision(self) -> None:
        assert readable_number("0.0615427182312") == "0.06154"
        assert readable_number("0.275944934835") == "0.2759"

    def test_a_whole_number_does_not_grow_a_decimal_point(self) -> None:
        assert readable_number("600") == "600"
        assert readable_number("600.0") == "600"

    def test_a_non_numeric_value_is_left_exactly_alone(self) -> None:
        # Workers report categorical outcomes too; mangling those would be worse
        # than the noise this function exists to remove.
        assert readable_number("converged") == "converged"
        assert readable_number("") == ""

    def test_the_deck_shows_the_rounded_value(self) -> None:
        deck = _render(_package())
        assert "0.2759" in deck
        assert "0.275944934835" not in deck, "raw float repr reached a slide"


class TestFiguresAreNamedNotPathed:
    def test_the_content_hash_prefix_and_directories_are_dropped(self) -> None:
        assert readable_artifact_name(GOVERNED) == "accuracy_vs_training_size.png"

    def test_a_bare_name_survives_untouched(self) -> None:
        assert readable_artifact_name("plot.png") == "plot.png"

    def test_the_deck_never_prints_the_governed_uri(self) -> None:
        deck = _render(_package())
        assert "accuracy_vs_training_size.png" in deck
        assert "/mnt/user-data/outputs/dbtl/how-large" not in deck, "the full artifact path reached a slide"


class TestAnEmptySectionKeepsItsSlide:
    """The empty states look like noise and are not.

    An earlier version of this change deleted them, and the existing suite
    caught it. Each of these sections is a **comment anchor**: a reviewer who
    wants to object that no limitations were recorded, or that no rerun
    procedure exists, needs somewhere to write that. And each absence is real
    information — Test cannot reach a verdict without headline metrics, and a
    missing rerun record is the `structured_rerun_spec` gate about to bite.

    Shortening the deck is the composition step's job, and it should shorten
    what is *verbose*, never delete the places a reviewer can speak.
    """

    def test_no_outcomes_still_gets_its_anchored_slide(self) -> None:
        deck = _render(_package(key_outcomes=()))
        assert 'data-slide-id="build-outcomes"' in deck
        assert "reported no numeric outcomes" in deck

    def test_no_rerun_procedure_still_gets_its_anchored_slide(self) -> None:
        deck = _render(_package())
        assert 'data-slide-id="build-rerun"' in deck

    def test_no_limitations_still_gets_its_anchored_slide(self) -> None:
        deck = _render(_package())
        assert 'data-slide-id="build-limitations"' in deck
        assert "None reported." in deck

    def test_a_repeated_limitation_is_listed_once(self) -> None:
        # Deduplication is safe where deletion is not: a worker restating one
        # caveat in two phases gives the reader nothing the first mention did.
        deck = _render(_package(limitations=("Pilot only.", "Pilot only.", "No linkage disequilibrium.")))
        assert deck.count("Pilot only.") == 1
        assert "No linkage disequilibrium." in deck

    def test_the_reviewed_document_is_still_stamped(self) -> None:
        assert "build-review-rev4.md" in _render(_package())
