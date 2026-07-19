import threading

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

    model.apply("history", "[03:41:12] you said: how's the agent doing")
    model.apply("history", "[03:41:20] entity> Gone quiet since yesterday.")
    model.apply("history", "===== 2026-07-18 =====")  # a file header is not conversation

    assert [(e["role"], e["stamp"], e["historical"]) for e in model.entries] == [
        ("you", "03:41:12", True),
        ("entity", "03:41:20", True),
    ]


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

    model.apply("line", "(listening on mic: Webcam 101)")
    model.apply("line", "")

    assert [(e["role"], e["text"]) for e in model.entries] == [("status", "(listening on mic: Webcam 101)")]


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
    # ONE real-Tk pass, withdrawn so nothing flashes on screen, and a single Tk lifecycle for the
    # whole process - Tk tolerates create/destroy/create sequences poorly enough to flake.
    from entity.gui import EntityWindow

    profile = tmp_path / "profile.md"
    profile.write_text(
        "## Life context (for awareness; do not raise unprompted)\n- new to the city\n\n"
        "## Goals\n- swim\n\n## Enhancements he wants for you (roadmap, not now)\n- better voice\n",
        encoding="utf-8",
    )
    logs = tmp_path / "agent-logs"
    logs.mkdir()
    (logs / "fixer.log").write_text("[10:00:00] ENTITY> fix the drive link\n", encoding="utf-8")

    feed = TranscriptFeed()
    done = threading.Event()
    submitted, mic_flips, stops = [], [], []
    ticks = [1000.0]
    window = EntityWindow(feed, on_stop=lambda: stops.append(True), on_close=lambda: None,
                          profile_path=profile, agent_logs_dir=logs, clock=lambda: "12:00:00",
                          persona="You are Entity. BREVITY IS YOUR MOST IMPORTANT RULE.",
                          now=lambda: ticks[0])
    try:
        window.withdraw()
        window.close_when(done)
        window.attach_mic(submit=submitted.append, set_recording=mic_flips.append)

        assert window.tab_labels()[:6] == ["1 · Conversation", "2 · Enhancements", "3 · Context",
                                           "4 · Goals", "5 · Projects", "6 · Persona"]
        assert "BREVITY" in window.persona_text()  # what it has been told, in the words it reads
        assert "mic off" in window.mic_button_text()  # the mic starts off

        feed.push("history", "[03:41:12] you said: how's the agent doing")
        feed.push("message", ("you", "pick up the drive work"))
        feed.push("message", ("entity", "Started 1 agent."))
        window._drain_once()
        shown = window.widget_text()
        assert "You · 03:41:12" in shown and "how's the agent doing" in shown  # yesterday, in place
        assert "You · 12:00:00" in shown and "Entity · 12:00:00" in shown  # names and times
        assert window.justify_of("you") == "right" and window.justify_of("entity") == "left"

        feed.push("draft", "add eggs")
        feed.push("draft", "and milk")
        window._drain_once()
        assert window.draft_text() == "add eggs and milk"  # dictation chunks joined readably
        feed.push("submit", "")
        window._drain_once()
        assert submitted == ["add eggs and milk"] and window.draft_text() == ""

        # One button: it IS the stop while Entity talks, and stopping leaves the mic off.
        feed.push("state", "speaking")
        window._drain_once()
        assert "stop" in window.mic_button_text()
        window.press_mic()
        assert stops == [True] and mic_flips == [False]

        for _ in range(window.SLOW_POLL_EVERY):  # let the slow poll fire once
            window._drain_once()
        assert "Agents" in window.tab_labels()  # one home, however many he ends up driving
        assert window.agent_tab_labels() == ["fixer  ✕"]  # closable, at his ask
        shown_log = window.agent_tab_text("fixer")
        assert "Entity · 10:00:00" in shown_log  # the agent exchange reads as a conversation too
        assert "fix the drive link" in shown_log

        # Closing one takes the tab away and archives its log, so it stays closed.
        window.close_agent_tab("fixer")
        assert window.agent_tab_labels() == []
        assert not (logs / "fixer.log").exists() and (logs / "closed" / "fixer.log").exists()
        for _ in range(window.SLOW_POLL_EVERY):
            window._drain_once()
        assert window.agent_tab_labels() == []  # and it doesn't come straight back
        assert window.section_text("4 · Goals").strip() == "- swim"
        assert window.section_text("3 · Context").strip() == "- new to the city"  # his category 3

        # Editing autosaves once he stops typing - there is no Save button to forget.
        window.set_section_text("4 · Goals", "- swim, three times a week")
        for _ in range(window.SLOW_POLL_EVERY):
            window._drain_once()
        assert "three times a week" not in profile.read_text(encoding="utf-8")  # still typing
        ticks[0] += window.AUTOSAVE_AFTER + 1
        for _ in range(window.SLOW_POLL_EVERY):
            window._drain_once()
        saved = profile.read_text(encoding="utf-8")
        assert "- swim, three times a week" in saved
        assert "- better voice" in saved and "- new to the city" in saved  # other sections untouched

        assert not window.ended
        done.set()
        window._drain_once()  # the conversation has wound down - the window ends itself
        assert window.ended
    finally:
        window.destroy()  # idempotent, so the happy path ending first is fine
