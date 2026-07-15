from entity.tts_system import SystemTTS


class FakeRun:
    def __init__(self):
        self.calls = []

    def __call__(self, script, text):
        self.calls.append((script, text))


def test_speak_passes_text_to_the_runner():
    run = FakeRun()
    tts = SystemTTS(run=run)

    tts.speak("hello there")

    assert len(run.calls) == 1
    assert run.calls[0][1] == "hello there"


def test_blank_text_is_not_spoken():
    run = FakeRun()
    tts = SystemTTS(run=run)

    tts.speak("   ")

    assert run.calls == []
