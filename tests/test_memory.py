from pathlib import Path

from entity.memory import (
    append_learned,
    compose_persona,
    lexicon_path,
    lexicon_terms,
    load_learned,
    load_lexicon,
    load_profile,
    parse_facts,
    save_translations,
    translation_pairs,
    user_name,
)


def test_lexicon_terms_takes_the_head_of_each_line_ignoring_glosses_and_comments():
    text = (
        "# lexicon\n"
        "\n"
        "Notecraft — the audio-memo app\n"
        "WaveShaper\n"
        "- Skylark — a project of theirs\n"
    )
    assert lexicon_terms(text) == ["Notecraft", "WaveShaper", "Skylark"]


def test_lexicon_terms_is_empty_for_blank_or_comment_only_text():
    assert lexicon_terms("") == []
    assert lexicon_terms("# just a header\n\n") == []


def test_translations_are_read_as_the_arrow_he_writes_them_with():
    text = (
        "# what it keeps mishearing\n"
        "\n"
        "cloud agent -> Claude agent\n"
        "- notecraf -> Notecraft\n"
        "work tree → worktree\n"
        "not a translation\n"
    )
    # Lowercased on the left, because that is the side it is looked up by; a line with no arrow on
    # it is not one, and is left out rather than guessed at.
    assert translation_pairs(text) == {
        "cloud agent": "Claude agent", "notecraf": "Notecraft", "work tree": "worktree",
    }


def test_translations_are_saved_as_the_file_he_can_read_back(tmp_path):
    path = tmp_path / "translations.md"

    save_translations("Notecraf -> Notecraft\n\ncloud agent -> Claude agent", path)

    assert translation_pairs(path.read_text(encoding="utf-8")) == {
        "notecraf": "Notecraft", "cloud agent": "Claude agent",
    }


def test_load_lexicon_is_empty_when_missing(tmp_path):
    assert load_lexicon(tmp_path / "nope.md") == ""


def test_compose_persona_folds_in_the_lexicon_under_its_own_framing():
    out = compose_persona("BASE", "", "", lexicon="Notecraft — the audio-memo app")

    assert "BASE" in out
    assert "Notecraft" in out
    # the lexicon is the user's vocabulary, framed to be recognised - NOT under the
    # life-context/therapy warning
    assert "vocabulary" in out.lower()
    # and it is NOT only their coined names: the domain terms of their fields belong here too, so
    # the framing must invite those rather than reading as "words the user made up"
    assert "domain" in out.lower()


def test_the_user_is_named_by_the_title_of_their_own_profile():
    # The name is the user's, so it lives in the user's file - never in the source.
    assert user_name("# Ada - standing profile\n\n41, lives in Lyon.\n") == "Ada"
    assert user_name("# Ada\n") == "Ada"


def test_a_user_with_no_profile_is_addressed_neutrally():
    # A fresh checkout has no profile yet; the persona still has to read as sentences.
    assert user_name("") == "the user"
    assert user_name("no heading here\n") == "the user"


def test_the_persona_is_addressed_to_whoever_the_profile_names():
    # The persona ships with a placeholder, never a name: composing it against a profile is what
    # decides who the Entity is for, so one source serves any user.
    out = compose_persona("You are {user}'s companion.", "# Ada - standing profile\n\nintro\n")

    assert "You are Ada's companion." in out
    assert "{user}" not in out


def test_load_profile_returns_empty_when_missing(tmp_path):
    assert load_profile(tmp_path / "nope.md") == ""


def test_load_profile_reads_the_file(tmp_path):
    path = tmp_path / "profile.md"
    path.write_text("Ada likes long walks.", encoding="utf-8")

    assert load_profile(path) == "Ada likes long walks."


def test_compose_persona_returns_base_when_nothing_to_add():
    assert compose_persona("BASE", "   ", "") == "BASE"


def test_compose_persona_folds_in_profile_and_learned_with_a_boundary_reminder():
    out = compose_persona("BASE", "They are learning the cello.", "They took the evening shift.")

    assert "BASE" in out
    assert "They are learning the cello." in out
    assert "evening shift" in out
    # the framing must remind the Entity not to turn the context into unprompted therapy
    assert "unprompted" in out.lower()


def test_parse_facts_reads_bullets_and_ignores_prose():
    text = "Here's what's new:\n- the move is booked for March\n* they picked the coastal route"

    assert parse_facts(text) == ["the move is booked for March", "they picked the coastal route"]


def test_parse_facts_returns_nothing_for_none():
    assert parse_facts("none") == []
    assert parse_facts("None.") == []


def test_append_learned_writes_facts_and_is_cumulative(tmp_path):
    path = tmp_path / "learned.md"

    append_learned(["they took up the cello"], path=path)
    append_learned(["the move is in March"], path=path)

    contents = load_learned(path)
    assert "they took up the cello" in contents
    assert "the move is in March" in contents
    assert contents.count("- ") >= 2


def test_append_learned_does_nothing_for_empty(tmp_path):
    path = tmp_path / "learned.md"

    append_learned([], path=path)

    assert not path.exists()


def test_the_lexicon_can_live_wherever_the_tool_that_shares_it_keeps_it(tmp_path):
    # One list, two tools: a term taught here should fix the other tool's transcripts too. That
    # tool may sync its state between machines, so the shared file can't be assumed to sit in this
    # repo's runtime dir, which no other machine can see. A pointer file says where it really is.
    shared = tmp_path / "synced" / "lexicon.md"
    pointer = tmp_path / "lexicon-path.txt"
    pointer.write_text(f"{shared}\n", encoding="utf-8")

    assert lexicon_path(pointer=pointer, default=tmp_path / "unused.md") == shared


def test_the_lexicon_sits_in_the_runtime_dir_when_nothing_points_elsewhere(tmp_path):
    default = tmp_path / "lexicon.md"

    assert lexicon_path(pointer=tmp_path / "absent.txt", default=default) == default


def test_a_pointer_written_with_a_tilde_reaches_the_home_directory(tmp_path):
    # It is written by hand, and by hand "~/notes/lexicon.md" is what anyone types; left literal
    # it would name a directory called "~" and the lexicon would silently read as empty.
    pointer = tmp_path / "lexicon-path.txt"
    pointer.write_text("~/notes/lexicon.md\n", encoding="utf-8")

    assert lexicon_path(pointer=pointer, default=tmp_path / "unused.md") == Path.home() / "notes" / "lexicon.md"


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
        "## Goals\n- swim\n\n## Enhancements you want (roadmap, not now)\n- better voice\n\n"
        "## Something after\n- untouched\n",
        encoding="utf-8",
    )

    append_enhancement("speaker enrollment", path=path)

    sections = profile_sections(path.read_text(encoding="utf-8"))
    assert "- better voice" in sections["Enhancements you want (roadmap, not now)"]
    assert "- [ ] speaker enrollment" in sections["Enhancements you want (roadmap, not now)"]
    assert sections["Something after"] == "- untouched"  # later sections undisturbed


def test_an_enhancement_is_filed_under_a_heading_that_merely_starts_with_the_word(tmp_path):
    # A profile writes its own headings and they run on ("Enhancements you want (roadmap, not
    # now)"), so the source can't carry the whole line. Matching the stem is what keeps a filing
    # inside the section that is already there instead of starting a rival one beside it.
    from entity.memory import append_enhancement, profile_sections

    path = tmp_path / "profile.md"
    path.write_text(
        "## Goals\n- swim\n\n## Enhancements you want (roadmap, not now)\n- better voice\n",
        encoding="utf-8",
    )

    append_enhancement("speaker enrollment", path=path)

    sections = profile_sections(path.read_text(encoding="utf-8"))
    assert "- [ ] speaker enrollment" in sections["Enhancements you want (roadmap, not now)"]
    assert list(sections) == ["Goals", "Enhancements you want (roadmap, not now)"]


def test_a_filed_enhancement_lands_as_an_unticked_box(tmp_path):
    # "As you check items off from the enhancements list, I don't want them deleted forever." So an
    # item is a checkbox from the moment it is filed, and finishing one ticks it in place.
    from entity.memory import append_enhancement, profile_sections

    path = tmp_path / "profile.md"
    path.write_text("## Enhancements\n- [ ] better voice\n", encoding="utf-8")

    append_enhancement("speaker enrollment", path=path)

    body = profile_sections(path.read_text(encoding="utf-8"))["Enhancements"]
    assert "- [ ] speaker enrollment" in body


def test_completing_an_enhancement_ticks_it_and_leaves_it_there(tmp_path):
    from entity.memory import complete_enhancement, profile_sections

    path = tmp_path / "profile.md"
    path.write_text(
        "## Enhancements\n- [ ] better voice\n- [ ] Only notice the user's voice: speaker enrollment\n",
        encoding="utf-8",
    )

    assert complete_enhancement("speaker enrollment", path=path) is True

    body = profile_sections(path.read_text(encoding="utf-8"))["Enhancements"]
    assert "- [x] Only notice the user's voice: speaker enrollment" in body  # ticked, and still readable
    assert "- [ ] better voice" in body  # and nothing else was touched


def test_an_item_that_isnt_there_is_reported_rather_than_invented(tmp_path):
    # A filing that silently misses is worse than one that fails: it reads as done and isn't.
    from entity.memory import complete_enhancement

    path = tmp_path / "profile.md"
    path.write_text("## Enhancements\n- [ ] better voice\n", encoding="utf-8")

    assert complete_enhancement("something nobody asked for", path=path) is False
    assert "[x]" not in path.read_text(encoding="utf-8")


def test_a_legacy_bullet_can_still_be_ticked(tmp_path):
    # The list predates the checkboxes, so most of it is plain bullets. Ticking one upgrades it
    # rather than refusing to find it.
    from entity.memory import complete_enhancement

    path = tmp_path / "profile.md"
    path.write_text("## Enhancements\n- better voice\n", encoding="utf-8")

    assert complete_enhancement("better voice", path=path) is True
    assert "- [x] better voice" in path.read_text(encoding="utf-8")


def test_a_section_reads_back_as_the_items_the_window_ticks():
    # The window draws boxes to click, so what it is handed is items - whether each is done and
    # what it says - rather than lines for it to parse a second time. The file stays markdown:
    # that same file is what the brain loads as standing context and what they read outside the app.
    from entity.memory import checklist_items

    stored = "- [ ] better voice\n- [x] speaker enrollment\n- a bullet from before the boxes\n\nprose"

    assert checklist_items(stored) == [
        {"done": False, "text": "better voice"},
        {"done": True, "text": "speaker enrollment"},
        {"done": False, "text": "a bullet from before the boxes"},
        {"done": False, "text": "prose"},   # any line with words on it is an item
    ]


def test_the_items_go_back_as_the_markdown_the_brain_reads():
    # A bullet written before the boxes existed comes back as `- [ ]`, so the list upgrades itself
    # the first time they touch it rather than needing a migration run over a personal file the
    # running app may be autosaving at that moment.
    from entity.memory import checklist_items, checklist_markdown

    items = checklist_items("- [x] done\n- a bullet from before the boxes\ntyped straight in")

    assert checklist_markdown(items) == (
        "- [x] done\n- [ ] a bullet from before the boxes\n- [ ] typed straight in"
    )


def test_several_lines_pasted_into_one_row_become_the_items_they_read_as():
    # A row is one line in the file, so a pasted block landing in one of them would otherwise be
    # stored as a bullet with newlines inside it - which reads back as items that have lost their
    # place in the list. It is split where they pasted the breaks, which is where they meant them.
    from entity.memory import checklist_markdown

    assert checklist_markdown([{"done": False, "text": "better voice\nspeaker enrollment"}]) == (
        "- [ ] better voice\n- [ ] speaker enrollment"
    )


def test_a_row_with_nothing_typed_into_it_yet_is_not_stored():
    # Pressing Enter makes the row before there are any words in it - and Enter is how every item
    # is made, so an untyped row is the normal state of the one they are about to fill in. Storing it
    # would leave a bullet with nothing after it sitting in their profile.
    from entity.memory import checklist_markdown

    assert checklist_markdown([{"done": False, "text": "better voice"},
                               {"done": False, "text": "  "}]) == "- [ ] better voice"


def test_ticking_and_typing_write_the_whole_list_back_into_its_section(tmp_path):
    from entity.memory import profile_sections, save_checklist

    path = tmp_path / "profile.md"
    path.write_text("# Ada\n\n## Goals\n- swim\n- cello\n\n## Projects\n- the atlas\n",
                    encoding="utf-8")

    save_checklist(path, "Goals", [{"done": True, "text": "swim"},
                                   {"done": False, "text": "cello, twice a week"}],
                   drawn=["swim", "cello"])

    sections = profile_sections(path.read_text(encoding="utf-8"))
    assert sections["Goals"] == "- [x] swim\n- [ ] cello, twice a week"
    assert sections["Projects"] == "- the atlas"  # the section beside it is untouched


def test_an_item_filed_while_the_page_sat_open_survives_the_next_thing_he_types(tmp_path):
    # Entity files enhancements into this same list, and the window is open all session. Every
    # keystroke writes the whole list back, so without this the next character they type deletes
    # whatever it filed a moment ago.
    from entity.memory import append_enhancement, profile_sections, save_checklist

    path = tmp_path / "profile.md"
    path.write_text("## Enhancements\n- [ ] better voice\n", encoding="utf-8")
    drawn = ["better voice"]        # what the page was showing when they started typing into it
    append_enhancement("filed while they typed", path=path)

    save_checklist(path, "Enhancements", [{"done": False, "text": "better voice, Cartesia"}],
                   drawn=drawn)

    assert profile_sections(path.read_text(encoding="utf-8"))["Enhancements"] == (
        "- [ ] better voice, Cartesia\n- [ ] filed while they typed"
    )


def test_an_item_he_edits_after_ticking_it_is_not_filed_a_second_time(tmp_path):
    # The first save upgrades `- swim` to `- [x] swim` in the file. Comparing what the page holds
    # against the stored LINES then reads that upgrade as an item nobody had seen, and files a
    # second copy of it beside their edit - so the comparison is on the words of an item instead.
    from entity.memory import profile_sections, save_checklist

    path = tmp_path / "profile.md"
    path.write_text("## Goals\n- swim\n", encoding="utf-8")

    save_checklist(path, "Goals", [{"done": True, "text": "swim"}], drawn=["swim"])
    save_checklist(path, "Goals", [{"done": True, "text": "swim, three times a week"}],
                   drawn=["swim"])

    assert profile_sections(path.read_text(encoding="utf-8"))["Goals"] == (
        "- [x] swim, three times a week"
    )


def test_a_section_can_be_rewritten_in_place_leaving_the_rest_alone(tmp_path):
    # The window's Goals/Projects/Enhancements panes are editable; saving one writes that section
    # back into the profile without disturbing a word of the others.
    from entity.memory import profile_sections, save_section

    path = tmp_path / "profile.md"
    path.write_text(
        "# Ada\nintro line\n\n## Goals\n- swim\n- cello\n\n## Projects (long-term)\n- the atlas\n",
        encoding="utf-8",
    )

    save_section(path, "Goals", "- swim, three times a week\n- cello")

    text = path.read_text(encoding="utf-8")
    sections = profile_sections(text)
    assert sections["Goals"] == "- swim, three times a week\n- cello"
    assert sections["Projects (long-term)"] == "- the atlas"
    assert text.startswith("# Ada\nintro line")  # the preamble survives too


def test_saving_a_section_that_is_not_there_yet_adds_it(tmp_path):
    from entity.memory import profile_sections, save_section

    path = tmp_path / "profile.md"
    path.write_text("## Goals\n- swim\n", encoding="utf-8")

    save_section(path, "Enhancements", "- dark mode")

    assert profile_sections(path.read_text(encoding="utf-8"))["Enhancements"] == "- dark mode"
