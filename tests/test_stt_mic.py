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


def test_listen_ends_the_turn_on_the_over_keyword():
    mic = FakeMic([_sp()] * 33)
    transcriber = FakeTranscriber("hello there over")

    stt = MicSTT(transcriber, mic, check_every=33, prompt="")

    assert stt.listen() == "hello there"


def test_listen_keeps_going_until_the_terminator_is_heard():
    mic = FakeMic([_sp()] * 66)
    transcriber = SeqTranscriber(["still talking", "still talking over"])

    stt = MicSTT(transcriber, mic, check_every=33, prompt="")

    assert stt.listen() == "still talking"
    assert transcriber.calls == 2  # checked, kept going, checked again


def test_listen_gives_up_and_sends_after_max_frames():
    mic = FakeMic([_sp()] * 40)
    transcriber = FakeTranscriber("no terminator spoken")

    stt = MicSTT(transcriber, mic, check_every=100, max_frames=40, prompt="")

    assert stt.listen() == "no terminator spoken"


def test_listen_waits_for_speech_before_transcribing():
    mic = FakeMic([_sil()] * 5 + [_sp()] * 33)
    transcriber = FakeTranscriber("finally over")

    stt = MicSTT(transcriber, mic, check_every=33, prompt="")

    # the leading silence is skipped; only the 33 speech frames are transcribed
    assert stt.listen() == "finally"
    assert transcriber.got.shape[0] == 480 * 33


def test_listen_aborts_without_transcribing_when_stop_is_set():
    class Flag:
        def is_set(self):
            return True

    mic = FakeMic([_sp()] * 40)
    transcriber = FakeTranscriber("should not run over")

    stt = MicSTT(transcriber, mic, prompt="", stop=Flag())

    assert stt.listen() == ""
    assert transcriber.got is None
