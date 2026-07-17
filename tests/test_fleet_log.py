from datetime import datetime

from entity.fleet_log import FleetLog, NullFleetLog


def ticking_clock(*times):
    """A clock that yields each of `times` in turn (one per log entry), holding the last."""
    remaining = list(times)
    held = [times[0]]

    def clock():
        if remaining:
            held[0] = remaining.pop(0)
        return held[0]

    return clock


def test_entity_and_agent_lines_are_timestamped_and_labeled(tmp_path):
    log_path = tmp_path / "session.log"
    clock = ticking_clock(datetime(2026, 7, 17, 8, 32, 1), datetime(2026, 7, 17, 8, 32, 45))
    log = FleetLog(log_path, clock=clock)

    log.entity("Started 1 agent. I'll speak up when one needs you.")
    log.agent("drive-link", "Found the bug in web.py.")

    assert log_path.read_text(encoding="utf-8") == (
        "[08:32:01] ENTITY: Started 1 agent. I'll speak up when one needs you.\n"
        "[08:32:45] AGENT drive-link: Found the bug in web.py.\n"
    )


def test_every_line_of_a_multi_line_message_is_stamped(tmp_path):
    log_path = tmp_path / "session.log"
    log = FleetLog(log_path, clock=ticking_clock(datetime(2026, 7, 17, 9, 0, 0)))

    log.agent("a", "first line\nsecond line")

    assert log_path.read_text(encoding="utf-8") == (
        "[09:00:00] AGENT a: first line\n"
        "[09:00:00] AGENT a: second line\n"
    )


def test_writes_append_across_calls(tmp_path):
    log_path = tmp_path / "session.log"
    log = FleetLog(log_path, clock=ticking_clock(datetime(2026, 7, 17, 1, 0, 0)))

    log.entity("one")
    log.entity("two")

    assert log_path.read_text(encoding="utf-8").splitlines() == [
        "[01:00:00] ENTITY: one",
        "[01:00:00] ENTITY: two",
    ]


def test_the_log_directory_is_created_if_missing(tmp_path):
    log_path = tmp_path / "fleet-logs" / "session.log"

    FleetLog(log_path, clock=ticking_clock(datetime(2026, 7, 17, 1, 0, 0))).entity("hi")

    assert log_path.exists()


def test_the_timestamp_format_is_configurable(tmp_path):
    log_path = tmp_path / "session.log"
    log = FleetLog(log_path, clock=ticking_clock(datetime(2026, 7, 17, 8, 5, 9)), timefmt="%Y-%m-%d %H:%M:%S")

    log.entity("dated")

    assert log_path.read_text(encoding="utf-8") == "[2026-07-17 08:05:09] ENTITY: dated\n"


def test_null_log_is_a_silent_no_op(tmp_path):
    log = NullFleetLog()

    log.entity("nothing")
    log.agent("a", "nothing")  # must not raise, must not create files

    assert list(tmp_path.iterdir()) == []
