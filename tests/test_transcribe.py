from entity.transcribe import ParakeetTranscriber


class FakeModel:
    def __init__(self, text):
        self._text = text
        self.calls = []

    def recognize(self, audio):
        self.calls.append(audio)
        return self._text


def test_transcribe_returns_stripped_recognized_text():
    model = FakeModel("  hello there  ")
    transcriber = ParakeetTranscriber(model=model)

    assert transcriber.transcribe("AUDIO") == "hello there"
    assert model.calls == ["AUDIO"]


def test_model_is_loaded_once_and_lazily():
    loads = []

    def loader(name):
        loads.append(name)
        return FakeModel("hi")

    transcriber = ParakeetTranscriber(loader=loader, model_name="M")
    assert loads == []  # nothing loaded at construction

    transcriber.transcribe("a")
    transcriber.transcribe("b")
    assert loads == ["M"]  # loaded once, on first use
