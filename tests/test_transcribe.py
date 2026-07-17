from entity.transcribe import CorrectingTranscriber, ParakeetTranscriber


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


class FakeTranscriber:
    def __init__(self, text):
        self._text = text
        self.warmed = False

    def transcribe(self, audio):
        return self._text

    def warmup(self):
        self.warmed = True


def test_correcting_transcriber_biases_output_toward_known_terms():
    inner = FakeTranscriber("let's open hideas")
    corrector = CorrectingTranscriber(inner, ["Notecraft"])

    assert corrector.transcribe("AUDIO") == "let's open Notecraft"


def test_correcting_transcriber_leaves_plain_speech_untouched():
    corrector = CorrectingTranscriber(FakeTranscriber("what's the weather"), ["Notecraft", "WaveShaper"])
    assert corrector.transcribe("AUDIO") == "what's the weather"


def test_correcting_transcriber_delegates_warmup_to_the_inner_model():
    inner = FakeTranscriber("hi")
    CorrectingTranscriber(inner, ["Notecraft"]).warmup()
    assert inner.warmed is True  # the 2.4 GB load still happens once at startup
