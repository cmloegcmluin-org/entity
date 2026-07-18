from datetime import datetime

from entity.transcript import Transcript


def _at(*times):
    """A clock that hands back the given datetimes in order, then repeats the last one."""
    moments = list(times)
    return lambda: moments.pop(0) if len(moments) > 1 else moments[0]


def test_every_line_is_stamped_with_the_time(tmp_path):
    path = tmp_path / "session.log"
    transcript = Transcript(path, clock=_at(datetime(2026, 7, 18, 0, 32, 19)))

    transcript.write("you said: pick up the drive work")

    assert path.read_text(encoding="utf-8").endswith("[00:32:19] you said: pick up the drive work\n")


def test_a_multi_line_entry_stamps_each_line(tmp_path):
    path = tmp_path / "session.log"
    Transcript(path, clock=_at(datetime(2026, 7, 18, 1, 0, 0))).write("first\nsecond")

    written = path.read_text(encoding="utf-8")
    assert "[01:00:00] first\n[01:00:00] second\n" in written


def test_a_prefix_is_applied_to_every_line(tmp_path):
    path = tmp_path / "session.log"
    Transcript(path, clock=_at(datetime(2026, 7, 18, 1, 0, 0))).write("a\nb", prefix="ENTITY: ")

    written = path.read_text(encoding="utf-8")
    assert "[01:00:00] ENTITY: a\n[01:00:00] ENTITY: b\n" in written


def test_the_date_is_written_once_and_again_when_the_day_rolls_over(tmp_path):
    path = tmp_path / "session.log"
    transcript = Transcript(path, clock=_at(
        datetime(2026, 7, 17, 23, 59, 0), datetime(2026, 7, 17, 23, 59, 30), datetime(2026, 7, 18, 0, 0, 1),
    ))

    transcript.write("late")
    transcript.write("later")
    transcript.write("past midnight")  # a session that runs over the day boundary says so

    assert path.read_text(encoding="utf-8").count("===== 2026-07-17 =====") == 1
    assert "===== 2026-07-18 =====" in path.read_text(encoding="utf-8")


def test_the_directory_is_created_if_it_is_not_there(tmp_path):
    path = tmp_path / "transcripts" / "session.log"

    Transcript(path).write("anything")

    assert path.exists()
