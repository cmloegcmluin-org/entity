from datetime import datetime

from excephalon.transcript import Transcript


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
    from excephalon.transcript import SESSION_MARK, past_lines

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
    from excephalon.transcript import SESSION_MARK, past_lines

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
    from excephalon.transcript import past_lines

    assert past_lines(tmp_path / "nowhere", current=None) == []


def test_a_recorded_line_reads_back_as_who_said_it_when_and_what():
    from excephalon.transcript import parse_line

    assert parse_line("[03:41:12] you said: pick up the drive work") == ("you", "03:41:12", "pick up the drive work")
    assert parse_line("[03:41:20] entity> Started 1 agent.") == ("excephalon", "03:41:20", "Started 1 agent.")
    assert parse_line("[03:43:03] entity (heads-up)> the fixer is done") == ("heads-up", "03:43:03", "the fixer is done")
    assert parse_line("[03:41:18] (thinking…)") == ("status", "03:41:18", "(thinking…)")


def test_what_an_agent_ran_reads_back_as_its_own_kind_with_its_shape_intact():
    from excephalon.transcript import parse_line

    # Its own role, because the tab draws the machinery differently from what the agent SAID -
    # a command and its output as messages would be one tinted box per line of a diff.
    assert parse_line("[08:20:15] WORK> Bash: python -m pytest -q") == (
        "work", "08:20:15", "Bash: python -m pytest -q")
    # And the indent is the structure: it is what puts output under the call that produced it.
    assert parse_line("[08:20:52] WORK>     358 passed in 4.41s") == (
        "work", "08:20:52", "    358 passed in 4.41s")


def test_a_day_header_reads_back_as_a_break_that_can_be_seen():
    from excephalon.transcript import parse_line

    role, _, text = parse_line("===== 2026-07-18 =====")

    # Scrolling back through every session is unreadable without a mark where one day ends. Its
    # own role, like the session break's: the contents list reads which day a session falls under
    # off these, and hunting for a date in the display text would be reading the label.
    assert role == "day" and "2026-07-18" in text


def test_a_session_mark_reads_back_as_its_own_break_and_not_as_another_date():
    from excephalon.transcript import DAY_BREAK, parse_line, SESSION_MARK

    role, _, text = parse_line(SESSION_MARK)

    # Telling the two apart is the whole point: a dated rule wearing a different word between the
    # dashes still reads as another date, which is what looked like a glitch in the first place.
    # The role says what it is, so nothing downstream has to recognise it by its own display text.
    assert role == "session"
    assert text != DAY_BREAK.format("session")
    assert not any(character.isdigit() for character in text)


def test_a_marker_with_a_blank_line_after_it_is_a_blank_line_not_the_marker():
    from excephalon.transcript import parse_line

    # A written line keeps no trailing space by the time it is read back, so a blank line under a
    # marker arrives as the bare marker - and turned up in the middle of the tab as a centred
    # "WORK>" and "AGENT>". Seen only by looking at the rendered pane; every test passed.
    assert parse_line("[09:29:25] AGENT> ") is None
    assert parse_line("[08:20:52] WORK> ") is None


def test_lines_that_are_not_conversation_read_back_as_nothing():
    from excephalon.transcript import parse_line

    assert parse_line("") is None
    assert parse_line("[03:41:12] ") is None


def test_the_last_conversation_can_be_read_back_as_turns(tmp_path):
    # "There should be a way to reload Excephalon so that it gets any fixes but without breaking the
    # current session." The half that breaks is the thread of the conversation: a fresh process
    # started with no memory of five minutes ago. The transcript already holds those turns; this
    # reads them back as (their words, the reply) pairs so a restarted brain can be seeded with them.
    from excephalon.transcript import recent_turns

    log = tmp_path / "session-20260719-200000.log"
    log.write_text(
        "===== 2026-07-19 =====\n"
        "[20:00:01] you said: how is the agent doing\n"
        "[20:00:02] Got it.\n"
        "[20:00:03] (thinking…)\n"
        "[20:00:05] entity> Still working - nothing new to report.\n"
        "[20:00:09]   [think 2.2s · speak 1.8s]\n"
        "[20:01:00] you said: okay tell it to rebase first\n"
        "[20:01:02] entity> Done - passed that along.\n",
        encoding="utf-8",
    )

    assert recent_turns(tmp_path) == [
        ("how is the agent doing", "Still working - nothing new to report."),
        ("okay tell it to rebase first", "Done - passed that along."),
    ]


def test_only_the_newest_session_is_read_back(tmp_path):
    # Continuity is with the conversation they just had, not with every session ever - the older
    # history is already in learned.md and on screen; re-feeding weeks of it would drown the seed.
    from excephalon.transcript import recent_turns

    (tmp_path / "session-20260718-100000.log").write_text(
        "[10:00:00] you said: old question\n[10:00:01] entity> old answer\n", encoding="utf-8")
    (tmp_path / "session-20260719-200000.log").write_text(
        "[20:00:00] you said: new question\n[20:00:01] entity> new answer\n", encoding="utf-8")

    assert recent_turns(tmp_path) == [("new question", "new answer")]


def test_recent_turns_keeps_only_the_tail_and_survives_absence(tmp_path):
    from excephalon.transcript import recent_turns

    lines = "".join(f"[20:00:0{n%10}] you said: q{n}\n[20:00:0{n%10}] entity> a{n}\n" for n in range(9))
    (tmp_path / "session-20260719-200000.log").write_text(lines, encoding="utf-8")

    assert recent_turns(tmp_path, keep=2) == [("q7", "a7"), ("q8", "a8")]
    assert recent_turns(tmp_path / "nowhere") == []


def test_a_question_the_session_died_on_is_not_paired_with_the_next_answer(tmp_path):
    # A crash between their words and the reply must not stitch their question to the answer of the
    # NEXT question - a seeded conversation where answers sit under the wrong questions is worse
    # than no seed at all.
    from excephalon.transcript import recent_turns

    (tmp_path / "session-20260719-200000.log").write_text(
        "[20:00:00] you said: the one it died on\n"
        "[20:01:00] you said: a fresh start\n"
        "[20:01:01] entity> the fresh answer\n",
        encoding="utf-8",
    )

    assert recent_turns(tmp_path) == [("a fresh start", "the fresh answer")]


def test_history_recorded_under_the_old_name_is_still_his_conversation(tmp_path):
    from excephalon.transcript import messages_in

    # Every message the app has ever stored carries `"role": "entity"`, and the .log lines it
    # wrote carry an "entity> " prefix. The displayed name was never in either file - it is looked
    # up from the role when the page draws - so renaming what gets STORED would leave months of
    # his own conversations carrying a role nothing recognises: no name, not a message, no side to
    # sit on. "the conversation history had been rewritten. this is terrifying" was a far smaller
    # version of that. Both spellings read back as the one thing they always were.
    record = tmp_path / "session-20260101-120000.jsonl"
    record.write_text(
        '{"at": "2026-01-01 12:00:00", "role": "you", "text": "hi"}\n'
        '{"at": "2026-01-01 12:00:01", "role": "entity", "text": "old spelling"}\n'
        '{"at": "2026-01-01 12:00:02", "role": "excephalon", "text": "new spelling"}\n',
        encoding="utf-8")

    spoken = [(role, text) for role, _, _, text in messages_in(record) if role != "you"]

    assert spoken == [("excephalon", "old spelling"), ("excephalon", "new spelling")]


def test_a_log_line_written_under_either_name_reads_back_the_same():
    from excephalon.transcript import parse_line

    assert parse_line("[12:00:01] entity> old") == ("excephalon", "12:00:01", "old")
    assert parse_line("[12:00:02] excephalon> new") == ("excephalon", "12:00:02", "new")
    # The heads-up prefix starts with the same word, so it has to be tried first or an unprompted
    # line comes back as an ordinary reply and stops being marked as one.
    assert parse_line("[12:00:03] entity (heads-up)> old") == ("heads-up", "12:00:03", "old")
    assert parse_line("[12:00:04] excephalon (heads-up)> new") == ("heads-up", "12:00:04", "new")
