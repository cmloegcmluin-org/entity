import threading
from types import SimpleNamespace

from entity.console import Console
from entity.gui import TranscriptFeed, TranscriptModel


def _model():
    return TranscriptModel(clock=lambda: "12:00:00")


def test_messages_become_entries_that_know_who_said_them():
    model = _model()

    model.apply("message", ("you", "pick up the drive work"))
    model.apply("message", ("entity", "on it"))

    assert [(e["role"], e["text"], e["stamp"]) for e in model.entries] == [
        ("you", "pick up the drive work", "12:00:00"),
        ("entity", "on it", "12:00:00"),
    ]


def test_past_sessions_read_back_as_the_conversation_they_were():
    model = _model()

    model.apply("history", "===== 2026-07-18 =====")  # the day, marking where these sessions start
    model.apply("history", "[03:41:12] you said: how's the agent doing")
    model.apply("history", "[03:41:20] entity> Gone quiet since yesterday.")

    assert [(e["role"], e["stamp"], e["historical"]) for e in model.entries] == [
        ("day", "2026-07-18", True),
        ("you", "03:41:12", True),
        ("entity", "03:41:20", True),
    ]


def test_an_agents_work_keeps_the_indent_that_puts_output_under_its_call():
    # Every other entry is stripped, because a message arrives with stray whitespace around it.
    # For the machinery the leading space IS the structure - strip it and a diff, a stack trace
    # and the command that produced them all land in one flat column.
    model = _model()

    model.apply("history", "[08:20:15] WORK> Bash: python -m pytest -q")
    model.apply("history", "[08:20:52] WORK>     358 passed in 4.41s")

    assert [(e["role"], e["text"]) for e in model.entries] == [
        ("work", "Bash: python -m pytest -q"),
        ("work", "    358 passed in 4.41s"),
    ]


def test_the_contents_list_names_each_session_by_the_day_and_time_it_opened():
    from entity.gui import sessions

    model = _model()
    for line in ("===== 2026-07-18 =====",
                 "[02:41:38] you said: morning",
                 "[02:42:10] entity> Morning.",
                 "===== session =====",
                 "[16:30:34] you said: back",
                 "===== session =====",
                 "===== 2026-07-19 =====",
                 "[03:39:22] you said: next day"):
        model.apply("history", line)

    # A session break carries no date of its own - the day is the last one above it, and the time
    # is the first thing said in the session, since that is what a contents line has to point at.
    assert [label for label, _ in sessions(model.entries)] == [
        "2026-07-18 02:41", "2026-07-18 16:30", "2026-07-19 03:39",
    ]
    assert [entry["role"] for _, entry in sessions(model.entries)] == ["day", "session", "session"]


def test_an_overwrite_run_collapses_onto_one_entry_like_the_terminal():
    model = _model()

    model.apply("message", ("entity", "Resting."))
    model.apply("overwrite", "\r(ignoring…)")
    model.apply("overwrite", "\r(ignoring… 2x)")
    model.apply("overwrite", "\r(ignoring… 3x)")

    assert len(model.entries) == 2  # one live counter, not three lines
    assert model.entries[-1]["text"] == "(ignoring… 3x)"


def test_a_closed_overwrite_run_stays_and_new_messages_follow_it():
    model = _model()

    model.apply("overwrite", "\r(ignoring…)")
    model.apply("overwrite", "\n")
    model.apply("message", ("you", "hey Entity"))

    assert [e["text"] for e in model.entries] == ["(ignoring…)", "hey Entity"]


def test_startup_narration_shows_as_status_and_blank_lines_are_dropped():
    model = _model()

    model.apply("line", "(listening on mic: Onboard 101)")
    model.apply("line", "")

    assert [(e["role"], e["text"]) for e in model.entries] == [("status", "(listening on mic: Onboard 101)")]


def test_the_feed_carries_ops_across_threads_in_order():
    feed = TranscriptFeed()

    def push(start):
        for index in range(start, start + 50):
            feed.push("message", ("you", f"line {index}"))

    threads = [threading.Thread(target=push, args=(base,)) for base in (0, 100)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(feed.drain()) == 100  # nothing lost
    assert feed.drain() == []  # and drained means drained


def test_the_console_drives_the_conversation_without_the_window_parsing_prefixes():
    feed = TranscriptFeed()
    console = Console(echo=lambda _: None, overwrite=lambda t: feed.push("overwrite", t),
                      messages=lambda role, text: feed.push("message", (role, text)))

    console.heard("what can you do")
    console.reply("plenty")
    console.ignored()
    console.ignored()
    console.heads_up("the fixer agent is done")

    model = _model()
    for op, payload in feed.drain():
        model.apply(op, payload)

    assert [(e["role"], e["text"]) for e in model.entries] == [
        ("you", "what can you do"),
        ("entity", "plenty"),
        ("status", "(ignoring… 2x)"),
        ("heads-up", "the fixer agent is done"),
    ]


def test_the_real_window_renders_a_thread_takes_edits_and_ends_with_the_conversation(tmp_path):
    # ONE real-Tk pass, laid out for real but fully transparent so nothing shows on screen, and a
    # single Tk lifecycle for the whole process - Tk tolerates create/destroy/create sequences
    # poorly enough to flake. Laid out for real because a withdrawn window reports every width as
    # 1, and the bubbles can only be checked where they actually landed.
    from entity.gui import EntityWindow

    profile = tmp_path / "profile.md"
    profile.write_text(
        "## Life context (for awareness; do not raise unprompted)\n- new to the city\n\n"
        "## Goals\n- learn to swim\n\n## Enhancements you want (roadmap, not now)\n- better voice\n",
        encoding="utf-8",
    )
    learned = tmp_path / "learned.md"
    learned.write_text("- prefers metric units" + chr(10), encoding="utf-8")
    logs = tmp_path / "agent-logs"
    logs.mkdir()
    (logs / "fixer.log").write_text(
        "[10:00:00] ENTITY> fix the drive link\n"
        "[10:00:01] (waking it up)\n"
        "[10:00:02] WORK> Bash: python -m pytest -q\n"
        "[10:00:03] WORK>     358 passed in 4.41s\n"
        "[10:00:04] AGENT> Green. Committing.\n",
        encoding="utf-8")

    feed = TranscriptFeed()
    done = threading.Event()
    submitted, mic_flips, stops = [], [], []
    ticks = [1000.0]
    chord = SimpleNamespace(started=0, stopped=0)
    chord.start = lambda: setattr(chord, "started", chord.started + 1)
    chord.stop = lambda: setattr(chord, "stopped", chord.stopped + 1)
    window = EntityWindow(feed, on_stop=lambda: stops.append(True), on_close=lambda: None,
                          profile_path=profile, agent_logs_dir=logs, clock=lambda: "12:00:00",
                          persona="You are Entity. BREVITY IS YOUR MOST IMPORTANT RULE.",
                          learned_path=learned, now=lambda: ticks[0], chord=chord)
    try:
        window.hide()
        window.close_when(done)
        window.attach_mic(submit=submitted.append, set_recording=mic_flips.append)

        assert window.tab_labels()[:7] == ["1 · Conversation", "2 · Enhancements", "3 · Context",
                                           "4 · Goals", "5 · Projects", "6 · Persona", "7 · Memory"]
        assert "BREVITY" in window.persona_text()  # what it has been told, in the words it reads
        assert "mic off" in window.mic_button_text()  # the mic starts off

        # Every session ever recorded arrives in one batch as the window opens. Only the newest
        # page is built: a bubble is two real widgets, a thousand of them cost a second, and the
        # archive grows every day - so opening must not get slower every day with it.
        for index in range(120):
            feed.push("history", f"[03:{index // 60:02d}:{index % 60:02d}] you said: old {index}")
        window._drain_once()
        assert window.waiting() == 80 and len(window.bubble_geometry()) == 40
        assert "old 0" in window.widget_text()  # held back, but still all of it to copy

        # A message arriving now must not throw the held past away - it is the very next thing
        # that happens when the window opens, and it would make the whole scrollback unreachable.
        feed.push("message", ("entity", "still here"))
        window._drain_once()
        assert window.waiting() == 80 and len(window.bubble_geometry()) == 41

        # Scrolling back to the top builds the page above it - a page, not everything.
        window.scroll_to_top()
        assert window.waiting() == 40 and len(window.bubble_geometry()) == 81

        # The contents list names each session, and clicking one goes there, building whatever of
        # the held past stands between here and it.
        assert window.contents() == ["03:00"]
        window.click_contents(0)
        assert window.waiting() == 0 and len(window.bubble_geometry()) == 121
        assert window.bubble_text(0) == "old 0"  # the oldest, at the top, where it belongs
        assert window.bubble_text(-1) == "still here"  # and the newest still last

        feed.push("history", "[03:41:12] you said: how's the agent doing")
        feed.push("message", ("you", "pick up the drive work"))
        feed.push("message", ("entity", "Started 1 agent."))
        feed.push("message", ("entity", "It is reading the log now, and I will say the moment it "
                                        "has anything worth showing you."))
        window._drain_once()
        shown = window.widget_text()
        assert "You · 03:41:12" in shown and "how's the agent doing" in shown  # yesterday, in place
        assert "You · 12:00:00" in shown and "Entity · 12:00:00" in shown  # names and times

        # Where the tinted boxes ACTUALLY landed in a real 980x760 window - measured off the
        # widgets, because what a bubble was configured to be is not what the eye receives.
        pane, placed = window.pane_width(), window.bubble_geometry()[-4:]
        assert [role for role, _, _ in placed] == ["you", "you", "entity", "entity"]
        assert max(width for _, _, width in placed) <= pane * 0.56  # each at most about half
        assert placed[-1][2] >= pane * 0.45  # and a long one does fill its column
        assert all(x + width >= pane - 14 for role, x, width in placed if role == "you")
        assert all(x <= 14 for role, x, _ in placed if role == "entity")

        # Hovering a message offers a copy button beside the bubble - never over it. The two
        # sides are worked out differently, and `place` measures from inside the pane's padding
        # while the bubble's own position reads from outside it, so both are checked.
        assert window.hover_gap(-3) > 0  # a message of his, on the right
        assert window.hover_gap(-1) > 0  # one of Entity's, on the left
        assert window.hover_copies(-3) == "pick up the drive work"

        # And the words can still be got back out: dragging inside a bubble and hitting Ctrl-C
        # copies that message - the bubbles are where the conversation's text lives now.
        assert window.copy_from_bubble(-3, "1.0", "1.4") == "pick"

        # One right-click menu for the whole window, however many messages it holds. A menu per
        # bubble runs Tk out of menu handles once the scrollback goes back far enough, and then
        # the window refuses to open at all - which is what loading every session ever exposed.
        assert window.menu_count() == 1
        assert window.menu_labels() == ["Copy"]  # and it offers only that

        # The level meter spans its column. Fixed at 110px it stopped short of the buttons above
        # and below it, which are sized in characters and come out wider.
        # The level meter spans its column, and so does the contents list above it. Fixed at
        # 110px the meter stopped short of the buttons, which are sized in characters.
        mic, meter, submit, contents = window.control_widths()
        assert meter == mic == submit == contents

        # Dragging the window narrower re-measures every box, because a width fixed in pixels
        # stops being half of anything the moment the pane changes size.
        window._tk.geometry("620x760")
        window._tk.update()  # the real <Configure>, which is what re-fits them
        narrow, pane = window.bubble_geometry()[-4:], window.pane_width()
        assert pane < 640 and max(width for _, _, width in narrow) <= pane * 0.56
        assert all(x + width >= pane - 14 for role, x, width in narrow if role == "you")
        assert all(x <= 14 for role, x, _ in narrow if role == "entity")

        feed.push("draft", "add eggs")
        feed.push("draft", "and milk")
        window._drain_once()
        assert window.draft_text() == "add eggs and milk"  # dictation chunks joined readably
        feed.push("submit", "")
        window._drain_once()
        assert submitted == ["add eggs and milk"] and window.draft_text() == ""

        # Ctrl+Z. He wiped out everything he had typed and there was no getting it back - "hugely
        # frustrating". Typed a key at a time and wiped by a real keystroke, because undo has to
        # work against the stack real typing builds, not against one programmatic insert.
        window.type_in_draft("the whole thing I typed")
        window.wipe_draft()
        assert window.draft_text() == ""
        window.press_undo()
        assert window.draft_text() == "the whole thing I typed"
        window.wipe_draft()

        # One button: it IS the stop while Entity talks, and stopping leaves the mic off.
        feed.push("state", "speaking")
        window._drain_once()
        assert "stop" in window.mic_button_text()
        window.press_mic()
        assert stops == [True] and mic_flips == [False]

        for _ in range(window.SLOW_POLL_EVERY):  # let the slow poll fire once
            window._drain_once()
        assert "Agents" in window.tab_labels()  # one home, however many end up being driven
        assert window.agent_tab_labels() == ["fixer  ✕"]  # closable, on request
        shown_log = window.agent_tab_text("fixer")
        assert "Entity · 10:00:00" in shown_log  # the agent exchange reads as a conversation too
        assert "fix the drive link" in shown_log
        # What it RAN and what came back are in the tab too, not just the sentence it narrated -
        # and the output sits UNDER its call, which is the whole job the indent does.
        assert "Bash: python -m pytest -q\n    358 passed in 4.41s\n" in shown_log

        # And where they actually landed, measured off the widgets: the machinery reads down the
        # left margin like a terminal, while an ordinary status line stays centred. A diff or a
        # stack trace centred line by line is not something anyone can read.
        window.show_agent_tab("fixer")
        placed = dict(window.agent_tab_line_geometry("fixer"))
        assert placed["work"] <= 14
        assert placed["status"] > placed["work"] + 100  # centred, and nothing like the margin
        # Its words are still messages, in a tinted box - only the machinery is a bare line.
        assert [role for role, _, _ in window.agent_tab_geometry("fixer")] == ["you", "entity"]

        # Closing one takes the tab away and archives its log, so it stays closed.
        window.close_agent_tab("fixer")
        assert window.agent_tab_labels() == []
        assert not (logs / "fixer.log").exists() and (logs / "closed" / "fixer.log").exists()
        for _ in range(window.SLOW_POLL_EVERY):
            window._drain_once()
        assert window.agent_tab_labels() == []  # and it doesn't come straight back
        assert window.section_text("4 · Goals").strip() == "- learn to swim"
        assert window.section_text("3 · Context").strip() == "- new to the city"  # category 3

        # Editing autosaves once the typing stops - there is no Save button to forget.
        window.set_section_text("4 · Goals", "- learn to swim, three times a week")
        for _ in range(window.SLOW_POLL_EVERY):
            window._drain_once()
        assert "three times a week" not in profile.read_text(encoding="utf-8")  # still typing
        ticks[0] += window.AUTOSAVE_AFTER + 1
        for _ in range(window.SLOW_POLL_EVERY):
            window._drain_once()
        saved = profile.read_text(encoding="utf-8")
        assert "- learn to swim, three times a week" in saved
        assert "- better voice" in saved and "- new to the city" in saved  # others untouched

        # The enhancements list is a checklist he clicks, not markdown he decodes: the tab shows a
        # box per item, clicking one ticks it and strikes it through, and the file keeps the item -
        # "as you check items off from the enhancements list, I don't want them deleted forever."
        for _ in range(window.SLOW_POLL_EVERY):
            window._drain_once()
        assert window.section_text("2 · Enhancements").strip() == "☐ better voice"
        assert window.section_struck_rows("2 · Enhancements") == []

        window.click_checkbox("2 · Enhancements", 1)

        assert window.section_text("2 · Enhancements").strip() == "☑ better voice"
        assert window.section_struck_rows("2 · Enhancements") == [1]  # and it reads as done
        ticks[0] += window.AUTOSAVE_AFTER + 1
        for _ in range(window.SLOW_POLL_EVERY):
            window._drain_once()
        ticked = profile.read_text(encoding="utf-8")
        assert "- [x] better voice" in ticked  # ticked in the file, still there to read

        # What it has learned is visible the moment it lands, and an edit of it sticks.
        for _ in range(window.SLOW_POLL_EVERY):
            window._drain_once()
        assert "metric units" in window.memory_text()
        window.set_memory_text("- prefers metric units" + chr(10)
                               + "- and hates being read a wall of text")
        ticks[0] += window.AUTOSAVE_AFTER + 1
        for _ in range(window.SLOW_POLL_EVERY):
            window._drain_once()
        assert "hates being read a wall" in learned.read_text(encoding="utf-8")

        # The submit chord listens for as long as the window is open, and no longer: a global
        # keyboard hook outliving its window would keep eating Win+Enter everywhere.
        assert chord.started == 1 and chord.stopped == 0

        assert not window.ended
        done.set()
        window._drain_once()  # the conversation has wound down - the window ends itself
        assert window.ended
        assert chord.stopped == 1
    finally:
        window.destroy()  # idempotent, so the happy path ending first is fine
    assert chord.stopped == 1  # and destroying twice doesn't double-stop it
