import numpy as np

from entity.stt_mic import FRAME, MicSTT, _strip_terminator


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
    # two speech bursts split by a mid-thought pause; each burst is transcribed on its own and the
    # pieces join, so the turn only ends when the LATEST piece carries the terminator.
    mic = FakeMic([_sp()] * 4 + [_sil()] * 3 + [_sp()] * 4 + [_sil()] * 3)
    transcriber = SeqTranscriber(["still thinking", "it over"])
    stt = MicSTT(transcriber, mic, pause_frames=3, prompt="")

    assert stt.listen() == "still thinking it"  # the first pause did NOT end the turn
    assert transcriber.calls == 2


def test_each_pause_transcribes_only_new_audio_not_the_whole_buffer():
    # the bug that stalled and crashed long turns: every pause re-transcribed the ENTIRE growing
    # buffer, so work per pause climbed without bound. Each pause must hand the transcriber only
    # the audio captured since the previous pause.
    sizes = []

    class MeasuringTranscriber:
        def transcribe(self, audio):
            sizes.append(len(audio))
            return "chunk"

    mic = FakeMic([_sp()] * 4 + [_sil()] * 3 + [_sp()] * 5 + [_sil()] * 3)
    stt = MicSTT(MeasuringTranscriber(), mic, pause_frames=3, prompt="")
    stt.listen()

    assert sizes == [7 * FRAME, 8 * FRAME]  # NOT [7*FRAME, 15*FRAME] - the old buffer wasn't re-sent


def test_leading_silence_is_skipped():
    mic = FakeMic([_sil()] * 3 + [_sp()] * 4 + [_sil()] * 3)
    stt = MicSTT(FakeTranscriber("finally over"), mic, pause_frames=3, prompt="")

    assert stt.listen() == "finally"


def test_stream_end_returns_what_it_captured():
    # a real mic never ends, so nothing but "over" (or quitting) stops a turn; this only guards
    # the fallback for a finite source that runs out without a terminator.
    mic = FakeMic([_sp()] * 5)
    stt = MicSTT(FakeTranscriber("just some words"), mic, pause_frames=100, prompt="")

    assert stt.listen() == "just some words"


def test_listen_aborts_without_transcribing_when_stop_is_set():
    class Flag:
        def is_set(self):
            return True

    transcriber = FakeTranscriber("should not run over")
    stt = MicSTT(transcriber, FakeMic([_sp()] * 5), prompt="", stop=Flag())

    assert stt.listen() == ""


def test_every_captured_frame_is_recorded_to_disk():
    written = []

    class Rec:
        def write(self, frame):
            written.append(frame)

    mic = FakeMic([_sp()] * 3 + [_sil()] * 3)
    stt = MicSTT(FakeTranscriber("hi over"), mic, pause_frames=3, prompt="", recorder=Rec())

    assert stt.listen() == "hi"
    assert len(written) == 6  # every frame read went to the recorder, before anything else
