from entity.memory import (
    append_learned,
    compose_persona,
    load_learned,
    load_profile,
    parse_facts,
)


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
