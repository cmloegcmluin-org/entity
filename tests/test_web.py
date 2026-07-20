from entity.mirror import Mirror, TranscriptFeed, TranscriptModel
from entity.web import create_app


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

    for path in ("/", "/profile", "/persona", "/memory", "/agents"):
        page = client.get(path).get_data(as_text=True)
        assert page.count('class="btn topbtn') == 5  # every page reaches every other one
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
    assert "- swim, three times a week" in saved
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
                                  "checklist": "true",
                                  "body": "☑ hear only his voice\n☑ live captions\n☐ plain line"})

    saved = profile.read_text(encoding="utf-8")
    assert "- [x] live captions" in saved  # ticked, not removed - the record that it was done
    assert "- [ ] plain line" in saved     # and a plain line joined the list it was meant to
    assert "☑" not in saved                # what is drawn never reaches the file


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
