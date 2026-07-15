import numpy as np

from entity.stt_mic import MicSTT, _strip_terminator


class FakeMic:
    def __init__(self, frames):
        self._frames = list(frames)

    def frames(self):
        while self._frames:
            yield self._frames.pop(0)


class FakeTranscriber:
    def __init__(self, text):
        self._text = text
        self.got = None

    def transcribe(self, audio):
        self.got = audio
        return self._text


class SeqTranscriber:
    def __init__(self, texts):
        self._texts = list(texts)
        self.calls = 0

    def transcribe(self, audio):
        self.calls += 1
        return self._texts.pop(0)


def _sp(level=0.2, n=480):
    return np.full(n, level, dtype=np.float32)


def _sil(n=480):
    return np.zeros(n, dtype=np.float32)


def test_strip_terminator_handles_case_and_punctuation():
    assert _strip_terminator("Hey there Over.", "over") == "Hey there"
    assert _strip_terminator("no keyword here", "over") is None
    assert _strip_terminator("Over", "over") == ""


def test_pausing_after_over_ends_the_turn_and_fires_the_cue():
    fired = []
    mic = FakeMic([_sp()] * 5 + [_sil()] * 3)
    stt = MicSTT(FakeTranscriber("hello there over"), mic, pause_frames=3, prompt="", cue=lambda: fired.append(True))

    assert stt.listen() == "hello there"
    assert fired == [True]  # the "registered" cue fired the moment it caught the terminator


def test_a_thinking_pause_without_over_keeps_listening():
    mic = FakeMic([_sp()] * 4 + [_sil()] * 3 + [_sp()] * 4 + [_sil()] * 3)
    transcriber = SeqTranscriber(["still thinking", "still thinking it over"])
    stt = MicSTT(transcriber, mic, pause_frames=3, prompt="")

    assert stt.listen() == "still thinking it"  # the first pause did NOT end the turn
    assert transcriber.calls == 2


def test_leading_silence_is_skipped():
    mic = FakeMic([_sil()] * 3 + [_sp()] * 4 + [_sil()] * 3)
    stt = MicSTT(FakeTranscriber("finally over"), mic, pause_frames=3, prompt="")

    assert stt.listen() == "finally"


def test_max_frames_ends_the_turn_even_without_a_pause():
    mic = FakeMic([_sp()] * 10)  # continuous speech, no pause, no "over"
    stt = MicSTT(FakeTranscriber("going on and on"), mic, pause_frames=100, max_frames=10, prompt="")

    assert stt.listen() == "going on and on"


def test_listen_aborts_without_transcribing_when_stop_is_set():
    class Flag:
        def is_set(self):
            return True

    transcriber = FakeTranscriber("should not run over")
    stt = MicSTT(transcriber, FakeMic([_sp()] * 5), prompt="", stop=Flag())

    assert stt.listen() == ""
    assert transcriber.got is None
