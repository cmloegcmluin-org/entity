from entity.inbox_watcher import InboxWatcher
from entity.outbox import Outbox


class SpyMonitor:
    """Records the InboxWatcher's activity signals without any real timing."""

    def __init__(self):
        self.check_ins = []
        self.ticks = 0

    def checked_in(self, agent):
        self.check_ins.append(agent)

    def tick(self):
        self.ticks += 1


def test_a_new_complete_line_is_pushed_to_the_outbox(tmp_path):
    outbox = Outbox()
    watcher = InboxWatcher(tmp_path, outbox)
    (tmp_path / "auth-agent.txt").write_text("I need your call: JWT or sessions?\n", encoding="utf-8")

    watcher.poll_once()

    assert outbox.drain() == ["auth-agent: I need your call: JWT or sessions?"]


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
    assert outbox.drain() == ["agent: still typing this thought"]  # surfaces once complete


def test_lines_across_several_files_all_surface(tmp_path):
    outbox = Outbox()
    watcher = InboxWatcher(tmp_path, outbox)
    (tmp_path / "a.txt").write_text("agent A is ready for review\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("agent B hit a failing test\n", encoding="utf-8")

    watcher.poll_once()

    assert set(outbox.drain()) == {"a: agent A is ready for review", "b: agent B hit a failing test"}


def test_a_cleared_inbox_file_resyncs_from_the_top(tmp_path):
    # Inboxes are append-only in normal use, but if one is cleared and reused, a shrink below where
    # we'd read tells us to resync so the next line isn't lost.
    outbox = Outbox()
    watcher = InboxWatcher(tmp_path, outbox)
    f = tmp_path / "agent.txt"
    f.write_text("first question\n", encoding="utf-8")
    watcher.poll_once()
    assert outbox.drain() == ["agent: first question"]

    f.write_text("", encoding="utf-8")  # cleared (shrinks below our offset)
    watcher.poll_once()
    f.write_text("second question\n", encoding="utf-8")
    watcher.poll_once()

    assert outbox.drain() == ["agent: second question"]


def test_blank_lines_are_ignored(tmp_path):
    outbox = Outbox()
    watcher = InboxWatcher(tmp_path, outbox)
    (tmp_path / "agent.txt").write_text("\n  \nreal message\n\n", encoding="utf-8")

    watcher.poll_once()

    assert outbox.drain() == ["agent: real message"]


def test_a_check_in_is_reported_to_the_monitor_when_an_agent_writes(tmp_path):
    monitor = SpyMonitor()
    watcher = InboxWatcher(tmp_path, Outbox(), monitor=monitor)
    (tmp_path / "auth-agent.txt").write_text("I hit a failing test\n", encoding="utf-8")

    watcher.poll_once()

    assert monitor.check_ins == ["auth-agent"]


def test_each_poll_ticks_the_monitor(tmp_path):
    monitor = SpyMonitor()
    watcher = InboxWatcher(tmp_path, Outbox(), monitor=monitor)

    watcher.poll_once()
    watcher.poll_once()

    assert monitor.ticks == 2


def test_a_freshly_appearing_file_registers_the_agent_before_its_first_full_line(tmp_path):
    # An agent that creates its inbox file and then hangs mid-sentence has written no complete
    # line, but it must still be watched for silence — its appearance is the first check-in.
    monitor = SpyMonitor()
    watcher = InboxWatcher(tmp_path, Outbox(), monitor=monitor)
    (tmp_path / "stalled.txt").write_text("starting work on the", encoding="utf-8")  # no newline

    watcher.poll_once()

    assert monitor.check_ins == ["stalled"]


def test_files_present_before_startup_are_not_monitored(tmp_path):
    # Stale inboxes from a previous run shouldn't trigger silence warnings for agents long gone.
    (tmp_path / "old.txt").write_text("finished yesterday\n", encoding="utf-8")
    monitor = SpyMonitor()
    watcher = InboxWatcher(tmp_path, Outbox(), monitor=monitor)

    watcher.poll_once()

    assert monitor.check_ins == []


def test_a_multi_line_report_arrives_as_one_notice_not_line_by_line(tmp_path):
    # An agent overwrote its inbox file with a 30-line report; every line became its own spoken
    # heads-up, and they had to hit STOP for each one in turn. Now it is one notice, and the report
    # itself stays in that agent's tab.
    outbox = Outbox()
    watcher = InboxWatcher(tmp_path, outbox)
    (tmp_path / "fixer.txt").write_text(
        "IN PROGRESS - backfill. Found a leaking test that wrote into your real state folder.\n"
        "Root cause: build_app calls load_dotenv internally.\nFixed and committed as 91459e5.\n",
        encoding="utf-8",
    )

    watcher.poll_once()

    said = outbox.drain()
    assert said == ["fixer: IN PROGRESS - backfill. (the rest is in fixer's tab)"]
    assert "91459e5" not in said[0]  # its internals never reach them
