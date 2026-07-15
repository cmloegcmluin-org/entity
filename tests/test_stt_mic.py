import numpy as np

from entity.stt_mic import MicSTT
from entity.vad import VadSegmenter


class FakeMic:
    def __init__(self, frames):
        self._frames = list(frames)
        self.reads = 0

    def read(self):
        self.reads += 1
        return self._frames.pop(0)

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


def _sp(level=0.2, n=480):
    return np.full(n, level, dtype=np.float32)


def _sil(n=480):
    return np.zeros(n, dtype=np.float32)


def test_listen_captures_an_utterance_and_transcribes_it():
    mic = FakeMic([_sil(), _sp(), _sp(), _sil(), _sil(), _sil()])
    seg = VadSegmenter(threshold=0.05, silence_tail_frames=3, min_speech_frames=2)
    transcriber = FakeTranscriber("hello entity")

    stt = MicSTT(transcriber, mic, segmenter=seg, prompt="")

    assert stt.listen() == "hello entity"
    assert transcriber.got.shape[0] == 480 * 5  # the captured utterance was handed to the transcriber


def test_listen_returns_blank_when_the_stream_holds_no_speech():
    mic = FakeMic([_sil()] * 4)
    seg = VadSegmenter(threshold=0.05, silence_tail_frames=3, min_speech_frames=2)
    transcriber = FakeTranscriber("unused")

    stt = MicSTT(transcriber, mic, segmenter=seg, prompt="")

    assert stt.listen() == ""
    assert transcriber.got is None


def test_missing_segmenter_is_calibrated_from_ambient_frames():
    mic = FakeMic([_sil(), _sil(), _sil()] + [_sp()] * 2 + [_sil()] * 3)

    MicSTT(FakeTranscriber("x"), mic, calibration_frames=3, prompt="")

    assert mic.reads == 3  # construction sampled exactly the ambient frames it was told to


def test_listen_aborts_without_transcribing_when_stop_is_set():
    class Flag:
        def is_set(self):
            return True

    mic = FakeMic([_sp()] * 6 + [_sil()] * 4)
    seg = VadSegmenter(threshold=0.05, silence_tail_frames=3, min_speech_frames=2)
    transcriber = FakeTranscriber("should not run")

    stt = MicSTT(transcriber, mic, segmenter=seg, prompt="", stop=Flag())

    assert stt.listen() == ""
    assert transcriber.got is None
