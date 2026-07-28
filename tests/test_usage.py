import json
from datetime import datetime, timedelta, timezone

from entity.usage import UsageWatch, save_weekly_limit, week_tokens, weekly_limit


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


def test_week_tokens_counts_the_rolling_week_and_ignores_what_predates_it(tmp_path):
    now = datetime(2026, 7, 27, 20, 0, tzinfo=timezone.utc)
    fresh = (now - timedelta(days=2)).isoformat()
    stale = (now - timedelta(days=9)).isoformat()
    project = tmp_path / "some-project"
    project.mkdir()
    _record(project / "session.jsonl", [(fresh, 100), (stale, 700)])

    assert week_tokens(tmp_path, now=now) == 200  # input + output of the fresh line only


def test_week_tokens_with_no_records_is_zero(tmp_path):
    assert week_tokens(tmp_path / "nowhere") == 0


def test_the_weekly_line_reads_back_what_was_saved(tmp_path):
    line = tmp_path / "usage-weekly-limit.txt"
    save_weekly_limit(line, 30000000)

    assert weekly_limit(line) == 30000000
    assert weekly_limit(tmp_path / "unset.txt") is None


def test_the_watch_speaks_once_at_each_of_his_chosen_shares(tmp_path):
    # "say something aloud to me when I hit 50% of my weekly limit, also 80%, 90%, 95%, 98%, and
    # 99%" - each once, never repeated on the next poll.
    line = tmp_path / "usage-weekly-limit.txt"
    save_weekly_limit(line, 1000)
    outbox = Outbox()
    spent = [500]
    watch = UsageWatch(outbox, line, measure=lambda: spent[0])

    watch.poll_once()
    watch.poll_once()
    assert len(outbox.pushed) == 1 and "50%" in outbox.pushed[0]

    for tokens, expected in ((800, 2), (905, 3), (950, 4), (980, 5), (991, 6)):
        spent[0] = tokens
        watch.poll_once()
        watch.poll_once()
        assert len(outbox.pushed) == expected

    spent[0] = 992  # further spending inside the same threshold stays quiet
    watch.poll_once()
    assert len(outbox.pushed) == 6


def test_a_jump_across_several_shares_speaks_once_at_the_highest(tmp_path):
    line = tmp_path / "usage-weekly-limit.txt"
    save_weekly_limit(line, 1000)
    outbox = Outbox()
    watch = UsageWatch(outbox, line, measure=lambda: 960)

    watch.poll_once()

    assert len(outbox.pushed) == 1
    assert "96%" in outbox.pushed[0]


def test_a_new_week_of_spending_rearms_the_ladder(tmp_path):
    line = tmp_path / "usage-weekly-limit.txt"
    save_weekly_limit(line, 1000)
    outbox = Outbox()
    spent = [990]
    watch = UsageWatch(outbox, line, measure=lambda: spent[0])

    watch.poll_once()
    spent[0] = 200   # the rolling week rolled off: back under 40%
    watch.poll_once()
    spent[0] = 510
    watch.poll_once()

    assert len(outbox.pushed) == 2  # 99% once, then 50% again for the fresh week


def test_without_a_line_set_the_watch_stays_silent(tmp_path):
    outbox = Outbox()
    watch = UsageWatch(outbox, tmp_path / "unset.txt", measure=lambda: 10**9)

    watch.poll_once()

    assert outbox.pushed == []
