"""What the council agreed on, what it did not, and what is still open.

The chair already produced a synthesis, but as prose in a `summary`. A reviewer
reading it cannot tell a genuine convergence from an averaged-away disagreement,
which is the one thing the chair is explicitly told not to do — and the one
thing a reader has no way to check. "The council agreed" and "the council was
talked into agreeing" render identically.

So the disagreements are structured and named, beside the agreements, with the
decisions nobody could settle carried separately. A synthesis with no recorded
disagreement is a claim worth being suspicious of, and now it is visibly one.
"""

from __future__ import annotations

import json

import pytest

from deerflow.dbtl.consensus import (
    MAX_CONSENSUS_ITEMS,
    Consensus,
    parse_consensus,
)


def _payload(**overrides) -> dict:
    body = {
        "agreements": ["Randomized complete block is adequate at three sites."],
        "disagreements": [
            {
                "topic": "Number of seasons",
                "positions": ["Two seasons is enough for a pilot.", "Three seasons or the G×E claim is unsupportable."],
                "resolution": "Two seasons, with the G×E claim explicitly out of scope.",
            }
        ],
        "open_questions": ["Which sites can guarantee irrigation control?"],
    }
    body.update(overrides)
    return body


class TestReadingAConsensus:
    def test_it_reads_the_three_parts(self):
        consensus = parse_consensus(_payload())

        assert consensus is not None
        assert consensus.agreements == ("Randomized complete block is adequate at three sites.",)
        assert len(consensus.disagreements) == 1
        assert consensus.open_questions == ("Which sites can guarantee irrigation control?",)

    def test_a_disagreement_keeps_both_sides_and_how_it_was_settled(self):
        consensus = parse_consensus(_payload())

        item = consensus.disagreements[0]
        assert item.topic == "Number of seasons"
        assert len(item.positions) == 2
        assert "out of scope" in item.resolution

    def test_an_unresolved_disagreement_is_kept_rather_than_dropped(self):
        """A disagreement the chair could not settle is the most useful entry.

        Dropping it would leave a synthesis that reads as complete while the
        thing a reviewer most needs to look at has been quietly removed.
        """
        consensus = parse_consensus(
            _payload(
                disagreements=[
                    {
                        "topic": "Trait direction",
                        "positions": ["Higher is better.", "Lower is better under stress."],
                        "resolution": "",
                    }
                ]
            )
        )

        assert len(consensus.disagreements) == 1
        assert consensus.disagreements[0].resolution == ""
        assert consensus.has_unresolved is True

    def test_a_fully_settled_consensus_reports_nothing_unresolved(self):
        # Every disagreement resolved and no question left for the owner.
        assert parse_consensus(_payload(open_questions=[])).has_unresolved is False

    def test_an_open_question_also_counts_as_unresolved(self):
        consensus = parse_consensus(_payload(disagreements=[]))

        assert consensus.has_unresolved is True


class TestSuspiciousShapes:
    def test_a_consensus_with_no_disagreement_is_flagged(self):
        """Three independent positions and a red team that all agreed.

        Possible, and worth a reviewer's attention every time. The flag does not
        say the synthesis is wrong; it says nobody recorded an argument, which
        is the shape a rubber-stamped debate leaves behind.
        """
        consensus = parse_consensus(_payload(disagreements=[], open_questions=[]))

        assert consensus.unanimous is True

    def test_a_real_disagreement_is_not_flagged(self):
        assert parse_consensus(_payload()).unanimous is False

    def test_a_disagreement_with_one_side_is_not_a_disagreement(self):
        consensus = parse_consensus(_payload(disagreements=[{"topic": "Seasons", "positions": ["Two is enough."], "resolution": "Two."}]))

        assert consensus.disagreements == ()


class TestRefusing:
    @pytest.mark.parametrize("value", [None, "", [], "a string", 42])
    def test_a_non_object_is_no_consensus_at_all(self, value):
        assert parse_consensus(value) is None

    def test_an_entirely_empty_object_is_no_consensus(self):
        # Better to render nothing than an empty "Agreements / Disagreements"
        # scaffold that implies the chair considered and found none.
        assert parse_consensus({"agreements": [], "disagreements": [], "open_questions": []}) is None

    def test_junk_entries_are_dropped_without_losing_the_good_ones(self):
        consensus = parse_consensus(
            _payload(agreements=["A real agreement.", "", None, 7, "   "]),
        )

        assert consensus.agreements == ("A real agreement.",)


class TestBounds:
    def test_each_list_is_capped(self):
        consensus = parse_consensus(
            _payload(agreements=[f"Agreement {index}." for index in range(50)]),
        )

        assert len(consensus.agreements) <= MAX_CONSENSUS_ITEMS


class TestRoundTrip:
    def test_it_serializes_for_the_package_and_the_sheet(self):
        consensus = parse_consensus(_payload())

        encoded = json.loads(json.dumps(consensus.as_dict()))
        assert encoded["unanimous"] is False
        # The fixture leaves one question for the project owner.
        assert encoded["has_unresolved"] is True
        assert encoded["disagreements"][0]["topic"] == "Number of seasons"
        assert encoded["disagreements"][0]["resolved"] is True

    def test_an_empty_consensus_cannot_be_constructed_as_unanimous(self):
        """`unanimous` must mean "argued and agreed", not "said nothing"."""
        assert Consensus().unanimous is False


class TestTheReviewDocument:
    """The consensus is worthless if the reviewer meets it after the synthesis."""

    @staticmethod
    def _package(**consensus_overrides) -> dict:
        return {
            "stage_spec_key": "generic:design:v2",
            "cycle_id": "cycle-1",
            "cycle_db_revision": 3,
            "satisfies_gate": False,
            "results": [
                {
                    "status": "completed",
                    "summary": "The council settled on three sites over two seasons.",
                    "capability": "design_council_chair",
                    "agent_name": "general-purpose",
                    "is_trustworthy": True,
                    "consensus": parse_consensus(_payload(**consensus_overrides)).as_dict(),
                }
            ],
        }

    def _render(self, **overrides) -> str:
        from deerflow.dbtl.review_markdown import render_review_markdown

        return render_review_markdown(
            self._package(**overrides),
            data_filename="package.json",
            data_hash="a" * 64,
        )

    def test_disagreements_appear_before_the_synthesis(self):
        document = self._render()

        landed = document.index("Where the council landed")
        synthesis = document.index("The council settled on three sites")
        # A reader who has already accepted the synthesis has no use for the
        # argument that produced it.
        assert landed < synthesis

    def test_both_sides_of_a_disagreement_are_shown(self):
        document = self._render()

        assert "Two seasons is enough for a pilot." in document
        assert "Three seasons or the G×E claim is unsupportable." in document

    def test_an_unresolved_disagreement_says_so_in_words(self):
        document = self._render(
            disagreements=[
                {
                    "topic": "Trait direction",
                    "positions": ["Higher is better.", "Lower is better under stress."],
                    "resolution": "",
                }
            ]
        )

        assert "Not resolved" in document

    def test_a_unanimous_council_carries_a_caution(self):
        document = self._render(disagreements=[], open_questions=[])

        assert "did not really happen" in document

    def test_a_package_with_no_consensus_renders_no_empty_scaffold(self):
        from deerflow.dbtl.review_markdown import render_review_markdown

        package = self._package()
        package["results"][0].pop("consensus")

        document = render_review_markdown(package, data_filename="p.json", data_hash="b" * 64)

        assert "Where the council landed" not in document
