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


def test_heard_is_silent_when_show_heard_is_off():
    lines = []
    console = Console(echo=lines.append, show_heard=False)  # text mode - he can see what he typed

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
