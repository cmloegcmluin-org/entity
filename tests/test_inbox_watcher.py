from entity.inbox_watcher import InboxWatcher
from entity.outbox import Outbox


def test_a_new_complete_line_is_pushed_to_the_outbox(tmp_path):
    outbox = Outbox()
    watcher = InboxWatcher(tmp_path, outbox)
    (tmp_path / "auth-agent.txt").write_text("I need your call: JWT or sessions?\n", encoding="utf-8")

    watcher.poll_once()

    assert outbox.drain() == ["I need your call: JWT or sessions?"]


def test_content_written_before_watching_is_not_replayed(tmp_path):
    (tmp_path / "old.txt").write_text("stale question from before startup\n", encoding="utf-8")
    outbox = Outbox()
    watcher = InboxWatcher(tmp_path, outbox)  # seeds offsets past existing content

    watcher.poll_once()

    assert outbox.drain() == []  # only news that arrives while watching surfaces


def test_a_partial_line_waits_until_its_newline_arrives(tmp_path):
    outbox = Outbox()
    watcher = InboxWatcher(tmp_path, outbox)
    f = tmp_path / "agent.txt"
    f.write_text("still typing this th", encoding="utf-8")  # no newline yet

    watcher.poll_once()
    assert outbox.drain() == []  # a half-written line isn't spoken

    with open(f, "a", encoding="utf-8") as fh:
        fh.write("ought\n")
    watcher.poll_once()
    assert outbox.drain() == ["still typing this thought"]  # surfaces once complete


def test_lines_across_several_files_all_surface(tmp_path):
    outbox = Outbox()
    watcher = InboxWatcher(tmp_path, outbox)
    (tmp_path / "a.txt").write_text("agent A is ready for review\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("agent B hit a failing test\n", encoding="utf-8")

    watcher.poll_once()

    assert set(outbox.drain()) == {"agent A is ready for review", "agent B hit a failing test"}


def test_a_cleared_inbox_file_resyncs_from_the_top(tmp_path):
    # Inboxes are append-only in normal use, but if one is cleared and reused, a shrink below where
    # we'd read tells us to resync so the next line isn't lost.
    outbox = Outbox()
    watcher = InboxWatcher(tmp_path, outbox)
    f = tmp_path / "agent.txt"
    f.write_text("first question\n", encoding="utf-8")
    watcher.poll_once()
    assert outbox.drain() == ["first question"]

    f.write_text("", encoding="utf-8")  # cleared (shrinks below our offset)
    watcher.poll_once()
    f.write_text("second question\n", encoding="utf-8")
    watcher.poll_once()

    assert outbox.drain() == ["second question"]


def test_blank_lines_are_ignored(tmp_path):
    outbox = Outbox()
    watcher = InboxWatcher(tmp_path, outbox)
    (tmp_path / "agent.txt").write_text("\n  \nreal message\n\n", encoding="utf-8")

    watcher.poll_once()

    assert outbox.drain() == ["real message"]
