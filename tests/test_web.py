from entity.memory import profile_sections
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


def _rule_for(css, selector):
    """The declaration block served for exactly this selector, so an assertion names the rule it
    means rather than fishing for a substring anywhere in the stylesheet."""
    start = css.index(selector + " {")
    body = css.index("{", start) + 1
    return css[body:css.index("}", body)]


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


def test_the_bar_reaches_every_page_and_carries_the_restart_button(tmp_path):
    profile = tmp_path / "profile.md"
    profile.write_text("## Goals\n- swim\n\n## Projects\n- entity\n", encoding="utf-8")
    client = _client(profile_path=profile)

    pages = ("/", "/config", "/agents")
    for path in pages:
        page = client.get(path).get_data(as_text=True)
        for other in pages:
            assert f'href="{other}"' in page  # every page reaches every other one
        # One click from a landed fix to running it, wherever he happens to be looking.
        assert 'id="restart"' in page


def test_the_tabs_this_page_replaced_still_answer(tmp_path):
    # A window standing open across the update lands on the new page, not on a 404.
    client = _client()

    for old in ("/profile", "/persona", "/memory", "/translations"):
        answer = client.get(old)
        assert answer.status_code == 302
        assert answer.headers["Location"].endswith("/config")


def test_the_bar_stays_frozen_with_the_same_air_above_and_below_the_pills():
    # The reading pages scroll at the document level, so the bar is pinned over its own opaque
    # background. Its air is INSIDE it (padding, symmetric): as an outside margin it scrolled
    # away - "it also strangely scrolls down to remove the margin above the row of pills" - and
    # below the pills there was never any, the border sitting flush on their bottoms.
    css = _client().get("/static/app.css").get_data(as_text=True)

    frozen = _rule_for(css, "body.page .topbar, body.config .topbar")
    assert "position: sticky" in frozen
    assert "top: 0" in frozen
    assert "background:" in frozen  # opaque, or the scrolled content bleeds through the tabs
    assert "padding: 10px 0" in frozen  # the same air above the pills and below them


def test_the_profile_page_shows_its_sections_and_saves_one_back(tmp_path):
    profile = tmp_path / "profile.md"
    profile.write_text("## Enhancements they want for you (roadmap, not now)\n- better voice\n\n"
                       "## Goals\n- swim\n", encoding="utf-8")
    client = _client(profile_path=profile)

    page = client.get("/config").get_data(as_text=True)
    assert "better voice" in page and "swim" in page
    # Matched by prefix, since a profile glosses its own headings however it likes.
    assert 'data-heading="Enhancements they want for you (roadmap, not now)"' in page

    client.post("/profile", json={"heading": "Goals", "drawn": ["swim"],
                                  "items": [{"done": False, "text": "swim, three times a week"}]})

    saved = profile.read_text(encoding="utf-8")
    # A bullet written before the boxes existed comes back as an unticked one, so the list
    # upgrades itself the first time they touch it rather than needing a migration run.
    assert "- [ ] swim, three times a week" in saved
    assert "- better voice" in saved  # the section beside it is untouched


def test_the_enhancements_list_is_a_checklist_that_ticks_rather_than_deletes(tmp_path):
    profile = tmp_path / "profile.md"
    profile.write_text("## Enhancements they want for you (roadmap, not now)\n"
                       "- [x] hear only their voice\n- live captions\nplain line\n", encoding="utf-8")
    client = _client(profile_path=profile)

    page = client.get("/config").get_data(as_text=True)
    # A box to click, not `- [x]` spelled out for the reader to decode - and any line with words
    # on it is an item, since they are typed in plain.
    assert page.count("<input type=\"checkbox\"") == 3
    assert page.count('<li class="done">') == 1

    # Ticking one writes the whole list back as markdown, which is the form the brain reads. Saving
    # the enhancements list also numbers it, so every item comes back with a stable id.
    client.post("/profile", json={
        "heading": "Enhancements they want for you (roadmap, not now)",
        "drawn": ["hear only their voice", "live captions", "plain line"],
        "items": [{"id": None, "done": True, "text": "hear only their voice"},
                  {"id": None, "done": True, "text": "live captions"},
                  {"id": None, "done": False, "text": "plain line"}],
    })

    saved = profile.read_text(encoding="utf-8")
    assert "- [x] #2 live captions" in saved  # ticked, not removed - the record that it was done
    assert "- [ ] #3 plain line" in saved      # and a plain line joined the list it was meant to


def test_the_enhancements_page_shows_each_items_id(tmp_path):
    # "Add IDs to all of the enhancements so I can refer to them by ID." The number is drawn beside
    # the item and carried on the row, so a save sends it back and the same item keeps the same id.
    profile = tmp_path / "profile.md"
    profile.write_text("## Enhancements they want (roadmap, not now)\n"
                       "- [ ] #4 better voice\n- [x] #2 older idea\n", encoding="utf-8")
    client = _client(profile_path=profile)

    page = client.get("/config").get_data(as_text=True)
    assert 'data-id="4"' in page and "#4" in page
    assert 'data-id="2"' in page and "#2" in page


def test_saving_the_enhancements_page_numbers_a_new_row_but_leaves_goals_plain(tmp_path):
    # Only the enhancements list is numbered - that is the one he refers to by id. A new row he adds
    # to it is handed the next number; the other panes stay the plain lists they were.
    profile = tmp_path / "profile.md"
    profile.write_text("## Enhancements they want (roadmap, not now)\n- [ ] #1 better voice\n\n"
                       "## Goals\n- swim\n", encoding="utf-8")
    client = _client(profile_path=profile)

    client.post("/profile", json={
        "heading": "Enhancements they want (roadmap, not now)",
        "drawn": ["better voice"],
        "items": [{"id": 1, "done": False, "text": "better voice"},
                  {"id": None, "done": False, "text": "dark mode"}]})
    client.post("/profile", json={"heading": "Goals", "drawn": ["swim"],
                                  "items": [{"id": None, "done": False, "text": "swim, thrice"}]})

    saved = profile.read_text(encoding="utf-8")
    assert "- [ ] #1 better voice" in saved and "- [ ] #2 dark mode" in saved
    assert "- [ ] swim, thrice" in saved and "#" not in profile_sections(saved)["Goals"]


def test_completed_items_sit_in_a_collapsible_done_section_at_the_bottom(tmp_path):
    # "All the completed tasks went to a done section at the bottom that is collapsible." The done
    # ones fold away so the list he still has to act on is what he sees; the fold is a <details>, so
    # it opens on a click with no script of its own.
    import re

    profile = tmp_path / "profile.md"
    profile.write_text("## Enhancements they want (roadmap, not now)\n"
                       "- [ ] #1 still to do\n- [x] #2 finished one\n- [x] #3 also done\n\n"
                       "## Goals\n- [ ] run\n", encoding="utf-8")
    client = _client(profile_path=profile)

    page = client.get("/config").get_data(as_text=True)
    fold = re.search(r"<details[^>]*class=\"done-fold\".*?</details>", page, re.S)
    assert fold is not None
    body = fold.group(0)
    assert "finished one" in body and "also done" in body   # the done ones are folded away
    assert "still to do" not in body                          # the open one is not
    assert "Done" in body and "2" in body                     # the summary counts them


def test_every_translation_in_force_is_an_editable_row_with_no_labels_and_no_second_copy(tmp_path):
    # One styled list, edited in place: no "built in" tag ("it doesn't matter whether a
    # translation is 'built-in' or not; don't display that"), no plain-text duplicate of his own
    # rules beneath it, and each row carries the stock rule for its words so a save can write
    # exactly what differs from what ships.
    translations = tmp_path / "translations.md"
    translations.write_text("notecraf -> Notecraft\n", encoding="utf-8")
    client = _client(translations_path=translations, terms=["Notecraft", "Git Bash"])

    page = client.get("/config").get_data(as_text=True)
    assert "cloud agent" in page and "Claude agent" in page  # one that ships, shown unbadged
    assert "built in" not in page
    # His own rule appears on its one row only - in the words he sees and the row's memory of
    # them (data-heard) - never again in a plain-text box below.
    assert "notecraf" in page and page.count("notecraf") == 2
    assert 'data-translations' not in page                     # the raw textarea is gone
    assert 'id="add-swap"' in page                             # + makes the next empty row
    assert "Git Bash" in page                                  # what the fuzzy pass snaps to

    client.post("/translations", data={"body": "notecraf -> Notecraft\nhi deas -> Notecraft"})

    assert "hi deas -> Notecraft" in translations.read_text(encoding="utf-8")


def test_the_config_page_has_a_contents_column_and_a_credits_card(tmp_path):
    profile = tmp_path / "profile.md"
    profile.write_text("## Goals\n- swim\n", encoding="utf-8")
    client = _client(profile_path=profile,
                     usage_status=lambda: {"tokens": 123456, "budget": 500000})

    page = client.get("/config").get_data(as_text=True)

    assert 'id="toc"' in page                    # each card is one click away
    assert 'id="card-credits"' in page
    assert "123,456" in page                     # the five-hour estimate, readable
    assert 'value="500000"' in page              # the line he set, where he set it


def test_the_close_dialog_and_its_wiring_reach_every_page():
    # The X asks in the app's own styling now - the native confirm was a light-mode system box
    # inside a dark app - so the dialog and its script ride on the shared chrome.
    page = _client().get("/").get_data(as_text=True)

    assert 'id="veil"' in page
    assert "closing.js" in page


def test_quit_and_restart_reach_the_window_they_serve_under():
    ways = []
    client = _client(on_quit=lambda: ways.append("quit"), on_restart=lambda: ways.append("restart"))

    client.post("/quit")
    client.post("/restart")

    assert ways == ["quit", "restart"]


def test_the_usage_budget_is_saved_only_when_it_is_a_number(tmp_path):
    kept = []
    client = _client(save_usage_budget=kept.append)

    client.post("/usage-budget", data={"tokens": "500,000"})
    client.post("/usage-budget", data={"tokens": "half a million"})

    assert kept == [500000]


def test_every_section_of_the_profile_draws_boxes_not_raw_markdown(tmp_path):
    # "consistent styling of all the tabs (all checkboxes, same font)". Enhancements was the only
    # one with boxes; the other three showed them the markdown and left them to decode it.
    profile = tmp_path / "profile.md"
    profile.write_text("## Enhancements\n- better voice\n\n## Life context\n- new to the city\n\n"
                       "## Goals\n- swim\n\n## Projects\n- entity\n", encoding="utf-8")
    client = _client(profile_path=profile)

    page = client.get("/config").get_data(as_text=True)

    assert page.count('<ul class="checklist"') == 4  # every section, not just the one
    assert page.count('<input type="checkbox"') == 4

    # And a tick in any of them still writes markdown back, which is what the brain reads.
    client.post("/profile", json={"heading": "Goals", "drawn": ["swim"],
                                  "items": [{"done": True, "text": "swim"}]})

    assert "- [x] swim" in profile.read_text(encoding="utf-8")


def test_an_item_is_words_he_can_type_into_and_there_is_no_edit_as_text(tmp_path):
    # "I add new items, tab away, tab back, and they're just gone." The box to edit a section as
    # raw markdown was the only way to add one, and it lost what they typed - so the items
    # themselves are what they type into, and a new one is made by pressing Enter in the list.
    profile = tmp_path / "profile.md"
    profile.write_text("## Goals\n- swim\n\n## Projects\n- entity\n", encoding="utf-8")

    page = _client(profile_path=profile).get("/config").get_data(as_text=True)

    # The words of an item are the item - one editable span per row, no raw-markdown box.
    assert page.count('class="item" contenteditable="plaintext-only"') == 2
    assert "Edit as text" not in page


def test_entitys_own_standing_instructions_are_shown_and_saved_back(tmp_path):
    # The persona was the one config the window could read but never write - the very gap that let
    # Entity say it couldn't update itself. Now its own accreted instructions have an editable box,
    # like what it has learned, while the full composed persona stays shown read-only above it.
    additions = tmp_path / "persona.md"
    additions.write_text("- never read a commit hash aloud\n", encoding="utf-8")
    client = _client(persona_additions_path=additions)

    page = client.get("/config").get_data(as_text=True)
    assert "never read a commit hash aloud" in page   # in an editable box, not only the read-only text
    assert 'data-persona="true"' in page

    client.post("/persona", data={"body": "- never read a commit hash aloud\n- one line at night"})

    assert "one line at night" in additions.read_text(encoding="utf-8")


def test_what_entity_has_learned_is_read_and_written_back(tmp_path):
    learned = tmp_path / "learned.md"
    learned.write_text("- prefers metric units\n", encoding="utf-8")
    client = _client(learned_path=learned)

    assert "prefers metric units" in client.get("/config").get_data(as_text=True)

    client.post("/memory", data={"body": "- prefers metric units\n- hates a wall of text"})

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
    # swapped, so neither reads as the user talking to themselves.
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


def test_the_mic_is_waking_until_the_pump_first_reports():
    # Born "muted", the window enabled its record button on the first poll - seconds before the
    # mic's models had loaded, so clicks died silently and the button read as broken.
    feed = TranscriptFeed()
    mirror = Mirror(feed, clock=lambda: "12:00:00")
    client = _client(mirror.model, mirror=mirror)

    assert client.get("/messages").get_json()["state"] == "waking"

    feed.push("state", "muted")  # the pump's first act on starting: say how the mic stands

    assert client.get("/messages").get_json()["state"] == "muted"


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
    # They caught it inside one poll. Undoing it in the box would mean putting it there first, so
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


def test_only_what_was_offered_as_a_link_can_be_opened(tmp_path):
    opened = []
    client = _client(opener=opened.append)

    assert client.post("/open", data={"target": "https://ex.com/x"}).status_code == 204
    # A POST that opens whatever string it is handed is a way to run things by talking to the port.
    assert client.post("/open", data={"target": "not a link at all"}).status_code == 400

    # A real path with a space in it - the case that broke - opens, because the same rule that
    # offered it says it exists; an invented one with a space does not.
    spaced = tmp_path / "Field Notes"
    spaced.mkdir()
    assert client.post("/open", data={"target": str(spaced)}).status_code == 204
    assert client.post("/open", data={"target": str(tmp_path / "Made Up")}).status_code == 400

    assert opened == ["https://ex.com/x", str(spaced)]


def test_the_one_click_yes_and_the_bin_are_both_on_the_page():
    # Saying yes cost four gestures - mic on, the word, mic off, Submit - for about half their turns,
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
    assert (tmp_path / "agent-logs-archive" / "fixer.log").exists()
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


def test_the_clipboard_is_served_to_the_drafts_own_paste_menu():
    # The embedded browser gives the draft box no paste menu, so the page asks the app - which
    # runs on the same machine as the clipboard - instead of asking the browser for permission.
    client = _client(clipboard=lambda: "words he copied elsewhere")

    got = client.get("/clipboard").get_json()

    assert got == {"text": "words he copied elsewhere"}


def test_saving_the_enhancements_hands_back_each_rows_number():
    # "when I'm inputting new tickets here the ID doesn't appear at first" - the page needs the
    # number the save assigned, so a fresh row shows its id the moment it first saves.
    import tempfile
    from pathlib import Path

    profile = Path(tempfile.mkdtemp()) / "profile.md"
    profile.write_text("# P\n\n## Enhancements he wants for you (roadmap, not now)\n- [ ] #7 old one\n",
                       encoding="utf-8")
    client = _client(profile_path=profile)

    answer = client.post("/profile", json={
        "heading": "Enhancements he wants for you (roadmap, not now)",
        "items": [{"id": 7, "done": False, "text": "old one"},
                  {"id": None, "done": False, "text": "a brand new ask"}],
        "drawn": ["old one"],
    }).get_json()

    assert answer == {"ids": [7, 8]}
    assert "- [ ] #8 a brand new ask" in profile.read_text(encoding="utf-8")
