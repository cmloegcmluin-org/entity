from entity.tts_system import SystemTTS


class FakeRun:
    def __init__(self):
        self.calls = []

    def __call__(self, script, text, interrupt=None):
        self.calls.append((script, text, interrupt))


def test_speak_passes_text_to_the_runner():
    run = FakeRun()
    tts = SystemTTS(run=run)

    tts.speak("hello there")

    assert len(run.calls) == 1
    assert run.calls[0][1] == "hello there"


def test_speak_forwards_the_interrupt_so_the_voice_can_be_cut_off():
    run = FakeRun()
    tts = SystemTTS(run=run)
    marker = object()

    tts.speak("a long reply", interrupt=marker)

    assert run.calls[0][2] is marker


def test_blank_text_is_not_spoken():
    run = FakeRun()
    tts = SystemTTS(run=run)

    tts.speak("   ")

    assert run.calls == []


def test_this_desk_has_a_robot_voice_at_all():
    # The fallback only serves when the neural voice cannot be had - which is exactly when it has
    # to work. It knew one desk's voice (System.Speech, through PowerShell) and raised TTSError on
    # the other, so a Mac that failed to fetch Kokoro would have started up mute.
    from entity.tts_system import command_for

    argv, env, feed = command_for("hello there", rate=2)

    assert argv  # something on this machine can say a line
    # On neither desk does the text ride on the command line: it is whatever the brain just said,
    # and a command line is quoted, logged, and visible to anyone running `ps`.
    assert "hello there" not in " ".join(argv)
    assert env.get("ENTITY_TTS_TEXT") == "hello there" or feed == "hello there"
