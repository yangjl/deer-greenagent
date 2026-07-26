"""Human-meaningful output paths for stage review packages.

These files are browsed in Finder by the scientist who owns the project, so the
path has to say what the file is. Opaque hash tokens are unreadable, and a
reviewer cannot tell two of them apart.
"""

from deerflow.dbtl.review_paths import (
    slugify,
    stage_file_name,
    stage_output_dir,
)


class TestSlugify:
    def test_lowercases_and_hyphenates_words(self):
        assert slugify("Genomic Selection in Maize") == "genomic-selection-in-maize"

    def test_collapses_punctuation_and_runs(self):
        assert slugify("G2F  --  yield/leakage (2024)") == "g2f-yield-leakage-2024"

    def test_strips_leading_and_trailing_separators(self):
        assert slugify("  --drought--  ") == "drought"

    def test_bounds_length(self):
        assert len(slugify("x" * 200, max_length=24)) <= 24

    def test_reduces_a_dot_name_to_nothing(self):
        for value in ("..", ".", "./"):
            assert slugify(value) == ""

    def test_a_traversal_becomes_a_plain_segment(self):
        # Not empty, but no longer traversal: separators and dots are gone, so
        # the result cannot escape the directory it is joined onto.
        slug = slugify("../../etc")
        assert slug == "etc"
        assert "." not in slug and "/" not in slug

    def test_drops_non_ascii_rather_than_guessing_a_transliteration(self):
        # Dropped before hyphenation, so a removed accent leaves no stray
        # separator behind.
        assert slugify("maïs été") == "mas-t"

    def test_empty_input_gives_an_empty_slug(self):
        assert slugify("") == ""


class TestOutputDir:
    def test_uses_the_cycle_title_and_a_short_id(self):
        path = stage_output_dir(
            cycle_id="cycle-5486db1c-5f90-42c7-adbb-874288df4a06",
            cycle_title="Genomic selection in maize",
            stage="design",
        )
        assert path.as_posix() == "dbtl/genomic-selection-in-maize-5486db1c/design"

    def test_falls_back_to_the_cycle_id_when_there_is_no_title(self):
        path = stage_output_dir(cycle_id="cycle-1", cycle_title="", stage="design")
        assert path.as_posix() == "dbtl/cycle-1/design"

    def test_keeps_two_same_titled_cycles_apart(self):
        first = stage_output_dir(cycle_id="cycle-aaaaaaaa-1", cycle_title="Drought", stage="design")
        second = stage_output_dir(cycle_id="cycle-bbbbbbbb-2", cycle_title="Drought", stage="design")
        assert first != second

    def test_sanitizes_a_hostile_stage_or_id(self):
        path = stage_output_dir(cycle_id="../../etc/passwd", cycle_title="", stage="../design")
        assert ".." not in path.as_posix()

    def test_never_returns_an_absolute_path(self):
        path = stage_output_dir(cycle_id="/abs", cycle_title="", stage="design")
        assert not path.is_absolute()


class TestFileName:
    def test_names_the_review_document_readably(self):
        name = stage_file_name(stage="design", kind="review", revision=2, content_hash="fd616c669073abc")
        assert name == "design-review-rev2-fd616c.md"

    def test_names_the_structured_package_readably(self):
        name = stage_file_name(stage="design", kind="package", revision=2, content_hash="4819611e58e8abc")
        assert name == "design-package-rev2-481961.json"

    def test_the_meaningful_part_comes_before_the_disambiguator(self):
        # A short content suffix is still needed — two runs at the same revision
        # produce different documents — but it must not lead the name.
        name = stage_file_name(stage="design", kind="review", revision=7, content_hash="abcdef123456")
        assert name.startswith("design-review-rev7-")

    def test_tolerates_a_missing_revision(self):
        name = stage_file_name(stage="design", kind="review", revision=None, content_hash="abcdef123456")
        assert "revNone" not in name
        assert name == "design-review-abcdef.md"
