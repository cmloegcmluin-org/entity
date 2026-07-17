from entity.console import Console


def _recording():
    lines = []
    return lines, Console(echo=lines.append)


def test_heard_shows_what_he_said():
    lines, console = _recording()

    console.heard("turn on the lights")

    assert lines == ["you said: turn on the lights"]


def test_heard_is_silent_when_show_heard_is_off():
    lines = []
    console = Console(echo=lines.append, show_heard=False)  # text mode - he typed it, no need to echo

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
