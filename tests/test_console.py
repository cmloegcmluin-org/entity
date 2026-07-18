from entity.console import Console


def _recording():
    # One list for both seams, so the assertions see the real interleaving of whole lines and
    # in-place overwrites. An overwrite is recognisable by its leading carriage return.
    lines = []
    return lines, Console(echo=lines.append, overwrite=lines.append)


def test_heard_shows_what_he_said():
    lines, console = _recording()

    console.heard("turn on the lights")

    assert lines == ["you said: turn on the lights"]


def test_a_typed_run_narrates_neither_the_mic_nor_his_own_words():
    lines = []
    console = Console(echo=lines.append, voice=False)  # he has his own prompt and his words on screen

    console.listening()
    console.heard("typed input")

    assert lines == []


def test_thinking_shows_the_indicator():
    lines, console = _recording()

    console.thinking()

    assert lines == ["(thinking…)"]


def test_reply_is_shown_prefixed_so_he_can_read_it():
    lines, console = _recording()

    console.reply("the lights are on")

    assert any("the lights are on" in line for line in lines)
    assert lines[0].startswith("entity>")


def test_heads_up_is_marked_as_unprompted():
    lines, console = _recording()

    console.heads_up("the deploy agent needs your call")

    assert any("the deploy agent needs your call" in line for line in lines)
    assert any("heads-up" in line for line in lines)


def test_timing_shows_the_think_and_speak_durations():
    lines, console = _recording()

    console.timing(think=2.34, speak=1.51)

    assert any("think 2.3s" in line and "speak 1.5s" in line for line in lines)


def test_ignoring_says_it_heard_something_and_dropped_it():
    lines, console = _recording()

    console.ignored()

    assert lines == ["\r(ignoring…)"]


def test_what_is_printed_is_also_written_to_the_session_record():
    # The terminal scrolls away, and it was the only record of what he actually saw.
    recorded = []
    console = Console(echo=lambda _: None, record=recorded.append)

    console.heard("pick up the drive work")
    console.reply("on it")

    assert recorded == ["you said: pick up the drive work", "entity> on it\n"]


def test_a_typed_run_still_records_what_he_said_even_though_it_is_not_echoed():
    recorded, lines = [], []
    console = Console(echo=lines.append, record=recorded.append, voice=False)

    console.heard("typed input")

    assert lines == []  # his own typing isn't echoed back at him
    assert recorded == ["you said: typed input"]  # but the record still has his side of it


def test_a_run_of_ignores_is_recorded_once_as_a_tally_not_line_by_line():
    recorded = []
    console = Console(echo=lambda _: None, overwrite=lambda _: None, record=recorded.append)

    for _ in range(16):
        console.ignored()
    console.reply("back with you")

    assert recorded == ["(ignored 16 while asleep)", "entity> back with you\n"]


def test_a_run_of_ignores_collapses_onto_one_line_with_a_tally():
    lines, console = _recording()

    for _ in range(3):
        console.ignored()

    assert lines == ["\r(ignoring…)", "\r(ignoring… 2x)", "\r(ignoring… 3x)"]  # each rewrites the last


def test_a_reply_closes_the_ignore_run_so_it_does_not_land_on_the_counter():
    lines, console = _recording()

    console.ignored()
    console.reply("back with you")

    assert lines[1] == "\n"  # the counter line is terminated first
    assert lines[2].startswith("entity>")


def test_a_later_ignore_starts_a_fresh_count():
    lines, console = _recording()

    console.ignored()
    console.reply("back with you")
    console.ignored()

    assert lines[-1] == "\r(ignoring…)"  # not "2x" - that run ended when he was answered
