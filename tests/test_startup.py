from entity.startup import ScriptedFirstTurn, load_startup_instructions


class FakeSTT:
    def __init__(self, lines):
        self._lines = list(lines)

    def listen(self):
        return self._lines.pop(0)


def test_load_returns_stripped_contents_when_the_file_has_text(tmp_path):
    path = tmp_path / "startup.txt"
    path.write_text("  resume the the-tracker session and tail the log  \n", encoding="utf-8")

    assert load_startup_instructions(path) == "resume the the-tracker session and tail the log"


def test_load_returns_none_when_the_file_is_missing(tmp_path):
    assert load_startup_instructions(tmp_path / "nope.txt") is None


def test_load_returns_none_when_the_file_is_blank(tmp_path):
    path = tmp_path / "startup.txt"
    path.write_text("   \n\t\n", encoding="utf-8")

    assert load_startup_instructions(path) is None


def test_scripted_first_turn_plays_the_line_once_then_defers():
    stt = ScriptedFirstTurn(FakeSTT(["second", "third"]), "my standing kickoff")

    assert stt.listen() == "my standing kickoff"  # the file's instructions, as his first turn
    assert stt.listen() == "second"  # then the real STT takes over
    assert stt.listen() == "third"


def test_scripted_first_turn_is_transparent_when_there_is_nothing_to_play():
    stt = ScriptedFirstTurn(FakeSTT(["real one"]), None)

    assert stt.listen() == "real one"  # no file, so it never interposes


def test_scripted_first_turn_forwards_stt_capabilities_to_the_inner():
    # It wraps the STT the Conversation sees, so it must be transparent beyond listen(): the loop
    # reads caught_terminator off the STT (to ack a bare "over") and the voice-stop watcher looks up
    # catch_stop. If the wrapper shadowed them, both features would silently die in production.
    class Inner:
        caught_terminator = True

        def listen(self):
            return "x"

        def catch_stop(self, active):
            return "stopped"

    stt = ScriptedFirstTurn(Inner(), None)

    assert stt.caught_terminator is True
    assert stt.catch_stop(lambda: True) == "stopped"
