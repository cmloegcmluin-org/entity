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
    inner = FakeTranscriber("let's open notcraft")
    corrector = CorrectingTranscriber(inner, ["Notecraft"])

    assert corrector.transcribe("AUDIO") == "let's open Notecraft"


def test_correcting_transcriber_leaves_plain_speech_untouched():
    corrector = CorrectingTranscriber(FakeTranscriber("what's the weather"), ["Notecraft", "WaveShaper"])
    assert corrector.transcribe("AUDIO") == "what's the weather"


def test_correcting_transcriber_applies_the_translations_it_ships_with():
    # Nothing passed in: the mishearings counted in real sessions are corrected out of the box, so
    # the list on the window's page is the list actually in force.
    corrector = CorrectingTranscriber(FakeTranscriber("how's our cloud agent"), [])

    assert corrector.transcribe("AUDIO") == "how's our Claude agent"


def test_a_users_own_translations_are_applied_beside_the_ones_that_ship():
    corrector = CorrectingTranscriber(FakeTranscriber("open notecraf for the cloud agent"), [],
                                      translations={"notecraf": "Notecraft"})

    assert corrector.transcribe("AUDIO") == "open Notecraft for the Claude agent"


def test_correcting_transcriber_delegates_warmup_to_the_inner_model():
    inner = FakeTranscriber("hi")
    CorrectingTranscriber(inner, ["Notecraft"]).warmup()
    assert inner.warmed is True  # the 2.4 GB load still happens once at startup
