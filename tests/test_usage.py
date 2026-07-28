import json
from datetime import datetime, timedelta, timezone

from entity.usage import UsageWatch, block_tokens, budget_line, save_budget


class Outbox:
    def __init__(self):
        self.pushed = []

    def push(self, message, about=None, composed=False):
        self.pushed.append(message)


def _record(path, stamps_and_usage):
    lines = [
        json.dumps({"timestamp": stamp,
                    "message": {"usage": {"input_tokens": tokens, "output_tokens": tokens}}})
        for stamp, tokens in stamps_and_usage
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def test_block_tokens_counts_the_window_and_ignores_what_predates_it(tmp_path):
    now = datetime(2026, 7, 27, 20, 0, tzinfo=timezone.utc)
    fresh = (now - timedelta(hours=1)).isoformat()
    stale = (now - timedelta(hours=9)).isoformat()
    project = tmp_path / "some-project"
    project.mkdir()
    _record(project / "session.jsonl", [(fresh, 100), (stale, 700)])

    assert block_tokens(tmp_path, now=now) == 200  # input + output of the fresh line only


def test_block_tokens_with_no_records_is_zero(tmp_path):
    assert block_tokens(tmp_path / "nowhere") == 0


def test_the_budget_line_reads_back_what_was_saved(tmp_path):
    line = tmp_path / "usage-budget.txt"
    save_budget(line, 500000)

    assert budget_line(line) == 500000
    assert budget_line(tmp_path / "unset.txt") is None


def test_the_watch_speaks_at_eighty_percent_and_at_the_line_once_each(tmp_path):
    line = tmp_path / "usage-budget.txt"
    save_budget(line, 1000)
    outbox = Outbox()
    spent = [850]
    watch = UsageWatch(outbox, line, measure=lambda: spent[0])

    watch.poll_once()
    watch.poll_once()  # the same crossing again: silence, not nagging
    assert len(outbox.pushed) == 1
    assert "85%" in outbox.pushed[0]

    spent[0] = 1200
    watch.poll_once()
    assert len(outbox.pushed) == 2  # the line itself is the second and last word

    spent[0] = 300   # a new block of work re-arms the watch...
    watch.poll_once()
    spent[0] = 900
    watch.poll_once()
    assert len(outbox.pushed) == 3  # ...so the next crossing speaks again


def test_without_a_line_set_the_watch_stays_silent(tmp_path):
    outbox = Outbox()
    watch = UsageWatch(outbox, tmp_path / "unset.txt", measure=lambda: 10**9)

    watch.poll_once()

    assert outbox.pushed == []
