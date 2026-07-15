from entity.profile import compose_persona, load_profile


def test_load_profile_returns_empty_when_missing(tmp_path):
    assert load_profile(tmp_path / "nope.md") == ""


def test_load_profile_reads_the_file(tmp_path):
    path = tmp_path / "profile.md"
    path.write_text("the user likes long walks.", encoding="utf-8")

    assert load_profile(path) == "the user likes long walks."


def test_compose_persona_returns_base_when_profile_is_blank():
    assert compose_persona("BASE", "   ") == "BASE"


def test_compose_persona_appends_profile_with_a_boundary_reminder():
    out = compose_persona("BASE", "He is learning cello.")

    assert "BASE" in out
    assert "He is learning cello." in out
    # the framing must remind the Entity not to turn the context into unprompted therapy
    assert "unprompted" in out.lower()
