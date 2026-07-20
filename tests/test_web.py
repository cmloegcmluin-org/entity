from entity.mirror import Mirror, TranscriptFeed, TranscriptModel
from entity.web import create_app, persona_paragraphs


def _model(*lines):
    model = TranscriptModel(clock=lambda: "12:00:00")
    for line in lines:
        model.apply("history", line)
    return model


def _client(model=None, **wiring):
    wiring.setdefault("on_submit", lambda text: None)
    return create_app(model if model is not None else _model(), **wiring).test_client()


def test_the_page_hands_over_who_said_what_rather_than_transcript_lines():
    model = _model("===== 2026-07-18 =====",
                   "[02:41:38] you said: morning",
                   "[02:42:10] entity> Morning.")

    shown = _client(model).get("/messages").get_json()

    assert [entry["role"] for entry in shown["entries"]] == ["day", "you", "entity"]
    assert shown["entries"][1]["name"] == "You"  # who said it, resolved once, on the server
    assert shown["entries"][2]["name"] == "Entity"
    assert shown["sessions"] == [{"label": "2026-07-18 02:41", "at": 0}]


def test_a_poll_carries_only_what_the_page_has_not_drawn():
    # Four times a second against every session ever recorded, so it cannot hand back the lot.
    model = _model("===== 2026-07-18 =====",
                   "[02:41:38] you said: morning",
                   "[02:42:10] entity> Morning.")
    client = _client(model)

    shown = client.get("/messages?since=2").get_json()

    assert [entry["text"] for entry in shown["entries"]] == ["Morning."]
    assert (shown["at"], shown["total"]) == (2, 3)  # where it starts, and how much there now is
    assert client.get("/messages?since=99").get_json()["entries"] == []  # never past the end


def test_every_session_break_is_named_where_it_stands():
    # The breaks are identical dicts, so anything locating one by value found the first of them
    # and sent every row of the contents to the same place.
    model = _model("===== 2026-07-18 =====", "[02:41:38] you said: morning",
                   "===== session =====", "[16:30:34] you said: back",
                   "===== session =====", "[18:00:00] you said: evening")

    shown = _client(model).get("/messages").get_json()

    assert [session["at"] for session in shown["sessions"]] == [0, 2, 4]
    # And a break carries its own name, so it reads as the row that points at it.
    assert [entry["label"] for entry in shown["entries"] if entry["role"] == "session"] == [
        "2026-07-18 16:30", "2026-07-18 18:00",
    ]


def test_the_bar_reaches_every_page_that_used_to_be_a_tab(tmp_path):
    profile = tmp_path / "profile.md"
    profile.write_text("## Goals\n- swim\n\n## Projects\n- entity\n", encoding="utf-8")
    client = _client(profile_path=profile, persona="BREVITY IS YOUR MOST IMPORTANT RULE.")

    pages = ("/", "/profile", "/persona", "/memory", "/translations", "/agents")
    for path in pages:
        page = client.get(path).get_data(as_text=True)
        assert page.count('class="btn topbtn') == len(pages)  # every page reaches every other one
        assert f'href="{path}"' in page


def test_the_profile_page_shows_its_sections_and_saves_one_back(tmp_path):
    profile = tmp_path / "profile.md"
    profile.write_text("## Enhancements he wants for you (roadmap, not now)\n- better voice\n\n"
                       "## Goals\n- swim\n", encoding="utf-8")
    client = _client(profile_path=profile)

    page = client.get("/profile").get_data(as_text=True)
    assert "- better voice" in page and "- swim" in page
    # Matched by prefix, since a profile glosses its own headings however it likes.
    assert 'data-heading="Enhancements he wants for you (roadmap, not now)"' in page

    client.post("/profile", data={"heading": "Goals", "body": "- swim, three times a week"})

    saved = profile.read_text(encoding="utf-8")
    # A bullet written before the boxes existed comes back as an unticked one, so the list
    # upgrades itself the first time he touches it rather than needing a migration run.
    assert "- [ ] swim, three times a week" in saved
    assert "- better voice" in saved  # the section beside it is untouched


def test_the_enhancements_list_is_a_checklist_that_ticks_rather_than_deletes(tmp_path):
    profile = tmp_path / "profile.md"
    profile.write_text("## Enhancements he wants for you (roadmap, not now)\n"
                       "- [x] hear only his voice\n- live captions\nplain line\n", encoding="utf-8")
    client = _client(profile_path=profile)

    page = client.get("/profile").get_data(as_text=True)
    # A box to click, not `- [x]` spelled out for the reader to decode - and any line with words
    # on it is an item, since they are typed in plain.
    assert page.count("<input type=\"checkbox\"") == 3
    assert page.count('<li class="done">') == 1

    # Ticking one writes the whole list back as markdown, which is the form the brain reads.
    client.post("/profile", data={"heading": "Enhancements he wants for you (roadmap, not now)",
                                  "body": "☑ hear only his voice\n☑ live captions\n☐ plain line"})

    saved = profile.read_text(encoding="utf-8")
    assert "- [x] live captions" in saved  # ticked, not removed - the record that it was done
    assert "- [ ] plain line" in saved     # and a plain line joined the list it was meant to
    assert "☑" not in saved                # what is drawn never reaches the file


def test_every_translation_in_force_is_on_the_page_and_his_own_save_back(tmp_path):
    # "can we have an explicit list of translations I can see" - so the ones that ship are listed
    # beside his own, rather than being applied invisibly.
    translations = tmp_path / "translations.md"
    translations.write_text("hydeas -> Notecraft\n", encoding="utf-8")
    client = _client(translations_path=translations, terms=["Notecraft", "Git Bash"])

    page = client.get("/translations").get_data(as_text=True)
    assert "cloud agent" in page and "Claude agent" in page  # one that ships
    assert "hydeas" in page and "Notecraft" in page           # one of his
    assert "Notecraft" in page and "Git Bash" in page        # and what the fuzzy pass snaps to

    client.post("/translations", data={"body": "hydeas -> Notecraft\nhi deas -> Notecraft"})

    assert "hi deas -> Notecraft" in translations.read_text(encoding="utf-8")


def test_the_persona_breaks_where_it_shouts_and_not_one_word_is_lost():
    # Six thousand characters arrive on a single line. The only structure in them is the author's
    # own shouting - "BREVITY IS YOUR MOST IMPORTANT RULE" - so that is where it is broken.
    persona = ("You are Entity, their companion. BREVITY IS YOUR MOST IMPORTANT RULE. Keep every "
               "reply to two sentences. SURFACE FAILURES IMMEDIATELY. Say so in one line first.")

    blocks = persona_paragraphs(persona)

    assert [block["lead"] for block in blocks] == [
        "", "BREVITY IS YOUR MOST IMPORTANT RULE.", "SURFACE FAILURES IMMEDIATELY.",
    ]
    # Laid out, never rewritten: this is the exact text the brain reads, and a second edited copy
    # of it would drift from what it actually reads.
    rebuilt = " ".join(" ".join(f"{block['lead']} {block['body']}".split()) for block in blocks)
    assert rebuilt == " ".join(persona.split())


def test_shouting_inside_a_sentence_is_not_pulled_out_as_a_heading():
    # "A CORE part of your job is..." and "ONE EXCEPTION, and it overrides brevity:" both open
    # with capitals and neither is a heading - lifting one out would cut its sentence in half.
    blocks = persona_paragraphs("Keep it short. A CORE part of your job is running agents. "
                                "ONE EXCEPTION, and it overrides brevity: answer them.")

    assert [block["lead"] for block in blocks] == ["", "", ""]
    assert blocks[1]["body"].startswith("A CORE part of your job")


def test_a_paragraph_that_is_already_markdown_is_left_as_it_was():
    # His profile arrives inside the persona with headings and bullets of its own, on their own
    # lines. Those are already readable and must survive intact.
    blocks = persona_paragraphs("## Goals\n- swim\n- cello\n\nplain words after it")

    assert [block["body"] for block in blocks] == ["## Goals\n- swim\n- cello", "plain words after it"]
    assert all(block["lead"] == "" for block in blocks)


def test_every_section_of_the_profile_draws_boxes_not_raw_markdown(tmp_path):
    # "consistent styling of all the tabs (all checkboxes, same font)". Enhancements was the only
    # one with boxes; the other three showed him the markdown and left him to decode it.
    profile = tmp_path / "profile.md"
    profile.write_text("## Enhancements\n- better voice\n\n## Life context\n- new to the city\n\n"
                       "## Goals\n- swim\n\n## Projects\n- entity\n", encoding="utf-8")
    client = _client(profile_path=profile)

    page = client.get("/profile").get_data(as_text=True)

    assert page.count('<ul class="checklist"') == 4  # every section, not just the one
    assert page.count('<input type="checkbox"') == 4

    # And a tick in any of them still writes markdown back, which is what the brain reads.
    client.post("/profile", data={"heading": "Goals", "body": "☑ swim"})

    assert "- [x] swim" in profile.read_text(encoding="utf-8")


def test_what_entity_has_learned_is_read_and_written_back(tmp_path):
    learned = tmp_path / "learned.md"
    learned.write_text("- prefers teal\n", encoding="utf-8")
    client = _client(learned_path=learned)

    assert "prefers teal" in client.get("/memory").get_data(as_text=True)

    client.post("/memory", data={"body": "- prefers teal\n- hates a wall of text"})

    assert "hates a wall of text" in learned.read_text(encoding="utf-8")


def test_an_agents_exchange_reads_as_a_conversation_with_the_speakers_swapped(tmp_path):
    logs = tmp_path / "agent-logs"
    logs.mkdir()
    (logs / "fixer.log").write_text("[10:00:00] ENTITY> fix the drive link\n"
                                    "[10:00:31] AGENT> Found it - repointed.\n", encoding="utf-8")
    client = _client(agent_logs_dir=logs, clock=lambda: "12:00:00")

    assert 'data-agent="fixer"' in client.get("/agents").get_data(as_text=True)

    shown = client.get("/agents/fixer").get_json()
    # In an agent's thread the Entity is the one asking and the agent answers - the speakers are
    # swapped, so neither reads as the user talking to himself.
    assert [(entry["name"], entry["text"]) for entry in shown["entries"]] == [
        ("Entity", "fix the drive link"), ("fixer", "Found it - repointed."),
    ]


def test_the_poll_is_the_pump_and_carries_the_mic_and_what_dictation_typed():
    feed = TranscriptFeed()
    mirror = Mirror(feed, clock=lambda: "12:00:00")
    client = _client(mirror.model, mirror=mirror)

    feed.push("message", ("you", "morning"))
    feed.push("state", "recording")
    feed.push("level", 0.03)
    feed.push("draft", "add eggs")
    feed.push("draft", "and milk")

    shown = client.get("/messages").get_json()

    assert [entry["text"] for entry in shown["entries"]] == ["morning"]  # drained by the poll
    assert (shown["state"], shown["level"]) == ("recording", 0.03)
    assert shown["dictated"] == ["add eggs", "and milk"]
    # Taken, not read: handed over twice they would be typed into the box twice.
    assert client.get("/messages?since=1").get_json()["dictated"] == []


def test_the_poll_carries_the_sentence_he_is_still_in_the_middle_of():
    feed = TranscriptFeed()
    mirror = Mirror(feed, clock=lambda: "12:00:00")
    client = _client(mirror.model, mirror=mirror)

    feed.push("hearing", "Then tell me exactly what")

    # A state, not a hand-off: the line stands on screen until it grows or is taken down, so every
    # poll has to carry it - unlike the draft chunks, which are typed into the box exactly once.
    assert client.get("/messages").get_json()["hearing"] == "Then tell me exactly what"
    assert client.get("/messages").get_json()["hearing"] == "Then tell me exactly what"

    feed.push("hearing", "")

    assert client.get("/messages").get_json()["hearing"] == ""


def test_taking_back_what_he_just_said_reaches_the_box_it_was_typed_into():
    feed = TranscriptFeed()
    mirror = Mirror(feed, clock=lambda: "12:00:00")
    client = _client(mirror.model, mirror=mirror)

    feed.push("draft", "pick up the drive subfolder work")
    client.get("/messages")  # the page has it in the box now, so the box is where it is undone
    feed.push("retract", "")
    feed.push("draft", "pick up the Notecraft work")

    shown = client.get("/messages").get_json()

    assert (shown["retract"], shown["dictated"]) == (1, ["pick up the Notecraft work"])
    assert client.get("/messages").get_json()["retract"] == 0  # taken, not read - undone once


def test_a_chunk_taken_back_before_the_page_saw_it_is_never_typed_at_all():
    # He caught it inside one poll. Undoing it in the box would mean putting it there first, so
    # the page is simply never told about it.
    feed = TranscriptFeed()
    mirror = Mirror(feed, clock=lambda: "12:00:00")
    client = _client(mirror.model, mirror=mirror)

    feed.push("draft", "pick up the drive subfolder work")
    feed.push("retract", "")
    feed.push("draft", "pick up the Notecraft work")

    shown = client.get("/messages").get_json()

    assert (shown["retract"], shown["dictated"]) == (0, ["pick up the Notecraft work"])


def test_dictation_saying_over_sends_the_box_as_it_stands():
    feed = TranscriptFeed()
    mirror = Mirror(feed, clock=lambda: "12:00:00")
    client = _client(mirror.model, mirror=mirror)

    feed.push("submit", "")

    assert client.get("/messages").get_json()["send"] is True
    assert client.get("/messages").get_json()["send"] is False  # and only the once


def test_an_agent_that_is_not_in_the_log_folder_is_not_a_path_to_read(tmp_path):
    logs = tmp_path / "agent-logs"
    logs.mkdir()

    answer = _client(agent_logs_dir=logs, clock=lambda: "12:00:00").get("/agents/..%2Fprofile")

    assert answer.status_code == 404


def test_a_message_naming_a_path_hands_it_over_as_something_to_open():
    # Entity names paths and addresses constantly, and reading one off the screen to retype it is
    # exactly what this saves. The rules live in links.py; the page only draws what it is handed.
    named = r"C:\ada\runtime\task.md"
    model = _model(rf"[10:00:00] entity> Filed it at {named}, see https://ex.com/x")

    parts = _client(model).get("/messages").get_json()["entries"][0]["parts"]

    assert [part["link"] for part in parts if part["link"]] == [named, "https://ex.com/x"]
    # The sentence's own punctuation stays outside the link, and not one word is lost.
    assert "".join(part["text"] for part in parts).strip() == (
        f"Filed it at {named}, see https://ex.com/x")


def test_only_what_was_offered_as_a_link_can_be_opened():
    opened = []
    client = _client(opener=opened.append)

    assert client.post("/open", data={"target": "https://ex.com/x"}).status_code == 204
    # A POST that opens whatever string it is handed is a way to run things by talking to the port.
    assert client.post("/open", data={"target": "not a link at all"}).status_code == 400
    assert opened == ["https://ex.com/x"]


def test_the_one_click_yes_and_the_bin_are_both_on_the_page():
    # Saying yes cost four gestures - mic on, the word, mic off, Submit - for about half his turns,
    # and the bin beside it throws a draft away undoably. Both went missing in the port.
    page = _client().get("/").get_data(as_text=True)

    assert 'id="yes"' in page and 'id="bin"' in page


def test_closing_an_agent_archives_its_log_so_it_stays_closed(tmp_path):
    # The roster IS the log folder, so a log left in place comes straight back on the next poll.
    logs = tmp_path / "agent-logs"
    logs.mkdir()
    (logs / "fixer.log").write_text("[10:00:00] ENTITY> fix it\n", encoding="utf-8")
    client = _client(agent_logs_dir=logs, clock=lambda: "12:00:00")

    assert client.post("/agents/fixer/close").status_code == 204

    assert not (logs / "fixer.log").exists()
    assert (logs / "closed" / "fixer.log").exists()
    assert 'data-agent="fixer"' not in client.get("/agents").get_data(as_text=True)
    assert client.post("/agents/fixer/close").status_code == 404  # and it is not a path to touch


def test_the_win_enter_chord_reaches_the_page_as_one_send():
    # The chord cannot reach any window on this machine, so it arrives by keyboard hook and
    # crosses the feed. Every link of that chain but the hook itself is checked here, because the
    # port moved the far end of it from a Tk binding to a page poll.
    from entity.chord import ENTER, LWIN, SubmitChord

    feed = TranscriptFeed()
    mirror = Mirror(feed, clock=lambda: "12:00:00")
    client = _client(mirror.model, mirror=mirror)
    chord = SubmitChord(submit=lambda: feed.push("submit", ""), focused=lambda: True)

    chord.key(LWIN, released=False)
    chord.key(ENTER, released=False)

    assert client.get("/messages").get_json()["send"] is True
    assert client.get("/messages").get_json()["send"] is False  # and the box is sent once
