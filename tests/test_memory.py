from pathlib import Path

from entity.memory import (
    DEFAULT_LEXICON_PATH,
    append_learned,
    compose_persona,
    lexicon_terms,
    load_learned,
    load_lexicon,
    load_profile,
    parse_facts,
)


def test_lexicon_terms_takes_the_head_of_each_line_ignoring_glosses_and_comments():
    text = (
        "# the user's lexicon\n"
        "\n"
        "Notecraft — his audio-memo web app\n"
        "WaveShaper\n"
        "- Skylark — German 'exactly', a project of his\n"
    )
    assert lexicon_terms(text) == ["Notecraft", "WaveShaper", "Skylark"]


def test_lexicon_terms_is_empty_for_blank_or_comment_only_text():
    assert lexicon_terms("") == []
    assert lexicon_terms("# just a header\n\n") == []


def test_load_lexicon_is_empty_when_missing(tmp_path):
    assert load_lexicon(tmp_path / "nope.md") == ""


def test_compose_persona_folds_in_the_lexicon_under_its_own_framing():
    out = compose_persona("BASE", "", "", lexicon="Notecraft — his audio-memo web app")

    assert "BASE" in out
    assert "Notecraft" in out
    # the lexicon is his vocabulary, framed to be recognised - NOT under the life-context/therapy warning
    assert "vocabulary" in out.lower()
    # and it is NOT only his coined names: the domain terms of his fields belong here too, so the
    # framing must invite those rather than reading as "words the user made up"
    assert "domain" in out.lower()


def test_load_profile_returns_empty_when_missing(tmp_path):
    assert load_profile(tmp_path / "nope.md") == ""


def test_load_profile_reads_the_file(tmp_path):
    path = tmp_path / "profile.md"
    path.write_text("the user likes long walks.", encoding="utf-8")

    assert load_profile(path) == "the user likes long walks."


def test_compose_persona_returns_base_when_nothing_to_add():
    assert compose_persona("BASE", "   ", "") == "BASE"


def test_compose_persona_folds_in_profile_and_learned_with_a_boundary_reminder():
    out = compose_persona("BASE", "He is learning cello.", "He decided to try for a new role role.")

    assert "BASE" in out
    assert "He is learning cello." in out
    assert "new role" in out
    # the framing must remind the Entity not to turn the context into unprompted therapy
    assert "unprompted" in out.lower()


def test_parse_facts_reads_bullets_and_ignores_prose():
    text = "Here's what's new:\n- timeline is about 6 months\n* he chose the new role path"

    assert parse_facts(text) == ["timeline is about 6 months", "he chose the new role path"]


def test_parse_facts_returns_nothing_for_none():
    assert parse_facts("none") == []
    assert parse_facts("None.") == []


def test_append_learned_writes_facts_and_is_cumulative(tmp_path):
    path = tmp_path / "learned.md"

    append_learned(["he started the class again"], path=path)
    append_learned(["timeline is 6 months"], path=path)

    contents = load_learned(path)
    assert "he started the class again" in contents
    assert "timeline is 6 months" in contents
    assert contents.count("- ") >= 2


def test_append_learned_does_nothing_for_empty(tmp_path):
    path = tmp_path / "learned.md"

    append_learned([], path=path)

    assert not path.exists()


def test_the_lexicon_is_the_same_file_notecraft_reads():
    # One list, two tools: a term taught here has to fix his transcripts there too.
    # Notecraft runs on his MacBook as well as this PC, so the shared file sits in the
    # state folder it syncs between them (NOTECRAFT_STATE_DIR) — never in this repo's
    # runtime dir, which no other machine can see.
    assert DEFAULT_LEXICON_PATH == Path.home() / "Notecraft" / "state" / "lexicon.md"


def test_profile_sections_split_on_headings():
    from entity.memory import profile_sections

    text = "# Title\nintro\n\n## Goals\n- swim\n- cello\n\n## Projects (long-term)\n- the atlas\n"
    sections = profile_sections(text)

    assert sections["Goals"] == "- swim\n- cello"
    assert sections["Projects (long-term)"] == "- the atlas"


def test_append_enhancement_lands_inside_the_enhancements_section(tmp_path):
    # Filed by voice mid-session; the window re-reads the file, so the bullet must land INSIDE the
    # section it belongs to, not at the end of the file under some other heading.
    from entity.memory import append_enhancement, profile_sections

    path = tmp_path / "profile.md"
    path.write_text(
        "## Goals\n- swim\n\n## Enhancements he wants for you (roadmap, not now)\n- better voice\n\n"
        "## Something after\n- untouched\n",
        encoding="utf-8",
    )

    append_enhancement("speaker enrollment", path=path)

    sections = profile_sections(path.read_text(encoding="utf-8"))
    assert "- better voice" in sections["Enhancements he wants for you (roadmap, not now)"]
    assert "- speaker enrollment" in sections["Enhancements he wants for you (roadmap, not now)"]
    assert sections["Something after"] == "- untouched"  # later sections undisturbed
