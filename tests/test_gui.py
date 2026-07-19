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
    profile.write_text("## Goals\n- swim\n\n## Enhancements he wants for you (roadmap, not now)\n- better voice\n",
                       encoding="utf-8")
    logs = tmp_path / "agent-logs"
    logs.mkdir()
    (logs / "fixer.log").write_text("[10:00:00] ENTITY> fix the drive link\n", encoding="utf-8")

    feed = TranscriptFeed()
    done = threading.Event()
    submitted, mic_flips, stops = [], [], []
    window = EntityWindow(feed, on_stop=lambda: stops.append(True), on_close=lambda: None,
                          on_submit=submitted.append, on_mic=mic_flips.append,
                          profile_path=profile, agent_logs_dir=logs, clock=lambda: "12:00:00")
    try:
        window.withdraw()
        window.close_when(done)

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
        assert "⚙ fixer" in window.tab_labels()
        assert "fix the drive link" in window.agent_tab_text("fixer")
        assert window.section_text("Goals").strip() == "- swim"

        window.set_section_text("Goals", "- swim, three times a week")
        window.save_section_tab("Goals")
        assert "- swim, three times a week" in profile.read_text(encoding="utf-8")
        assert "- better voice" in profile.read_text(encoding="utf-8")  # other sections untouched

        assert not window.ended
        done.set()
        window._drain_once()  # the conversation has wound down - the window ends itself
        assert window.ended
    finally:
        window.destroy()  # idempotent, so the happy path ending first is fine
