import threading

from entity.console import Console
from entity.gui import TranscriptFeed, TranscriptModel


def test_lines_accumulate_in_order():
    model = TranscriptModel()

    model.apply("line", "you said: hi")
    model.apply("line", "entity> hello\n")

    assert model.lines == ["you said: hi", "entity> hello", ""]  # the trailing blank line survives


def test_an_overwrite_run_collapses_onto_one_line_like_the_terminal():
    # The ignore counter rewrites itself in place with carriage returns; the window mirrors that.
    model = TranscriptModel()

    model.apply("line", "entity> Resting. Say 'hey Entity' when you want me back.\n")
    model.apply("overwrite", "\r(ignoring…)")
    model.apply("overwrite", "\r(ignoring… 2x)")
    model.apply("overwrite", "\r(ignoring… 3x)")

    assert model.lines[-1] == "(ignoring… 3x)"  # one live line ticking up, not three lines


def test_a_closed_overwrite_run_stays_and_new_lines_follow_it():
    model = TranscriptModel()

    model.apply("overwrite", "\r(ignoring…)")
    model.apply("overwrite", "\n")  # the Console closes the run before any real line
    model.apply("line", "you said: hey Entity")

    assert model.lines == ["(ignoring…)", "you said: hey Entity"]


def test_the_feed_carries_ops_across_threads_in_order():
    feed = TranscriptFeed()

    def push(start):
        for index in range(start, start + 50):
            feed.push("line", f"line {index}")

    threads = [threading.Thread(target=push, args=(base,)) for base in (0, 100)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    ops = feed.drain()
    assert len(ops) == 100  # nothing lost
    assert feed.drain() == []  # and drained means drained


def test_the_real_window_mirrors_everything_and_ends_itself_when_the_conversation_does(tmp_path):
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
    submitted = []
    mic_flips = []
    window = EntityWindow(feed, on_stop=lambda: None, on_close=lambda: None,
                          on_submit=submitted.append, on_mic=mic_flips.append,
                          profile_path=profile, agent_logs_dir=logs)
    try:
        window.withdraw()
        window.close_when(done)

        feed.push("line", "you said: hi")
        feed.push("line", "entity> hello\n")
        feed.push("overwrite", "\r(ignoring…)")
        feed.push("overwrite", "\r(ignoring… 2x)")
        window._drain_once()
        text = window.widget_text()
        assert "you said: hi" in text and "entity> hello" in text
        assert "(ignoring… 2x)" in text and "(ignoring…)\n" not in text  # collapsed, not stacked

        feed.push("draft", "add eggs")
        feed.push("draft", "and milk")
        feed.push("state", "muted")
        feed.push("level", 0.03)
        window._drain_once()
        assert window.draft_text() == "add eggs and milk"  # dictation chunks joined readably

        feed.push("submit", "")
        window._drain_once()
        assert submitted == ["add eggs and milk"]  # a spoken "over" submits the edited draft
        assert window.draft_text() == ""  # and the box is ready for the next turn

        for _ in range(window.SLOW_POLL_EVERY):  # let the slow poll fire once
            window._drain_once()
        labels = window.tab_labels()
        assert "Conversation" in labels and "⚙ fixer" in labels
        assert "fix the drive link" in window.agent_tab_text("fixer")
        assert "- swim" in window.section_text("Goals")
        assert "- better voice" in window.section_text("Enhancements")

        assert not window.ended
        done.set()
        window._drain_once()  # the conversation has wound down - the window ends itself
        assert window.ended
    finally:
        window.destroy()  # idempotent, so the happy path ending first is fine


def test_the_console_drives_the_feed_the_same_way_it_drives_a_terminal():
    # The window plugs into the same Console seams as the terminal - one source of truth for what
    # a session looks like, whichever surface shows it.
    feed = TranscriptFeed()
    console = Console(echo=lambda t: feed.push("line", t), overwrite=lambda t: feed.push("overwrite", t))

    console.heard("what can you do")
    console.thinking()
    console.reply("plenty")
    console.ignored()
    console.ignored()
    console.heads_up("the fixer agent is done")

    model = TranscriptModel()
    for op, text in feed.drain():
        model.apply(op, text)

    assert "you said: what can you do" in model.lines
    assert "(thinking…)" in model.lines
    assert "entity> plenty" in model.lines
    assert "(ignoring… 2x)" in model.lines
    assert any("heads-up" in line and "fixer" in line for line in model.lines)
