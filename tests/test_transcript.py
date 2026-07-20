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


def test_past_lines_hands_back_every_session_ever_recorded(tmp_path):
    from entity.transcript import SESSION_MARK, past_lines

    for day in range(10, 16):  # more sessions than any window would have kept
        (tmp_path / f"session-202607{day}-010000.log").write_text(
            f"day {day} one\nday {day} two\n", encoding="utf-8")
    current = tmp_path / "session-20260718-010000.log"
    current.write_text("live - must not appear\n", encoding="utf-8")

    lines = past_lines(tmp_path, current=current)

    assert lines[:2] == ["day 10 one", "day 10 two"]  # scrolls back to the start of time
    assert lines[-1] == "day 15 two"
    said = [line for line in lines if line != SESSION_MARK]
    assert len(said) == 12  # every line of every session, nothing dropped off the top
    assert "live - must not appear" not in lines


def test_a_day_is_dated_once_however_many_sessions_it_held(tmp_path):
    from entity.transcript import SESSION_MARK, past_lines

    for name, day, said in (("session-20260718-010000.log", "2026-07-18", "morning"),
                            ("session-20260718-090000.log", "2026-07-18", "afternoon"),
                            ("session-20260719-010000.log", "2026-07-19", "next day")):
        (tmp_path / name).write_text(f"===== {day} =====\n[01:00:00] you said: {said}\n",
                                     encoding="utf-8")

    lines = past_lines(tmp_path, current=None)

    # The date says which day; the session mark says a new conversation started. Two sessions in
    # one day used to print that day's date twice, which reads as a glitch rather than a boundary.
    assert lines.count("===== 2026-07-18 =====") == 1
    assert lines.count(SESSION_MARK) == 2  # and each of the three sessions is still divided
    assert lines.index(SESSION_MARK) < lines.index("===== 2026-07-19 =====")


def test_past_lines_survives_an_empty_or_missing_directory(tmp_path):
    from entity.transcript import past_lines

    assert past_lines(tmp_path / "nowhere", current=None) == []


def test_a_recorded_line_reads_back_as_who_said_it_when_and_what():
    from entity.transcript import parse_line

    assert parse_line("[03:41:12] you said: pick up the drive work") == ("you", "03:41:12", "pick up the drive work")
    assert parse_line("[03:41:20] entity> Started 1 agent.") == ("entity", "03:41:20", "Started 1 agent.")
    assert parse_line("[03:43:03] entity (heads-up)> the fixer is done") == ("heads-up", "03:43:03", "the fixer is done")
    assert parse_line("[03:41:18] (thinking…)") == ("status", "03:41:18", "(thinking…)")


def test_a_day_header_reads_back_as_a_break_that_can_be_seen():
    from entity.transcript import parse_line

    role, _, text = parse_line("===== 2026-07-18 =====")

    # Scrolling back through every session is unreadable without a mark where one day ends. Its
    # own role, like the session break's: the contents list reads which day a session falls under
    # off these, and hunting for a date in the display text would be reading the label.
    assert role == "day" and "2026-07-18" in text


def test_a_session_mark_reads_back_as_its_own_break_and_not_as_another_date():
    from entity.transcript import DAY_BREAK, parse_line, SESSION_MARK

    role, _, text = parse_line(SESSION_MARK)

    # Telling the two apart is the whole point: a dated rule wearing a different word between the
    # dashes still reads as another date, which is what looked like a glitch in the first place.
    # The role says what it is, so nothing downstream has to recognise it by its own display text.
    assert role == "session"
    assert text != DAY_BREAK.format("session")
    assert not any(character.isdigit() for character in text)


def test_lines_that_are_not_conversation_read_back_as_nothing():
    from entity.transcript import parse_line

    assert parse_line("") is None
    assert parse_line("[03:41:12] ") is None
