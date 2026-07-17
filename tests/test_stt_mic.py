import threading

import numpy as np

from entity.stt_mic import FRAME, MicSTT, NoiseFloor, _is_backchannel, _strip_terminator


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


def test_is_backchannel_spots_filler_but_keeps_real_words_and_the_terminator():
    assert _is_backchannel("Mm-hmm. Yeah. Uh.", "over") is True
    assert _is_backchannel("yeah", "over") is True
    assert _is_backchannel("Hey, I'm confused.", "over") is False  # real words, not filler
    assert _is_backchannel("yeah over", "over") is False  # carries the terminator - a real end
    assert _is_backchannel("", "over") is False


def test_pure_backchannel_noise_is_dropped_from_the_turn():
    # a silent stretch that Parakeet hallucinates as "Mm-hmm." must not pollute the real turn.
    mic = FakeMic([_sp()] * 4 + [_sil()] * 3 + [_sp()] * 4 + [_sil()] * 3)
    transcriber = SeqTranscriber(["Mm-hmm.", "hello there over"])
    stt = MicSTT(transcriber, mic, pause_frames=3, prompt="", threshold=0.01)

    assert stt.listen() == "hello there"  # the hallucinated chunk was dropped, only real speech kept


def test_catch_stop_fires_on_a_spoken_stop_word():
    mic = FakeMic([_sil()] * 2 + [_sp()] * 4 + [_sil()] * 3)  # quiet, a burst, then a pause
    stt = MicSTT(FakeTranscriber("Stop!"), mic, pause_frames=3, prompt="")

    assert stt.catch_stop(lambda: True) is True  # he barked "stop" while it was talking


def test_catch_stop_ignores_ordinary_speech():
    mic = FakeMic([_sil()] * 2 + [_sp()] * 4 + [_sil()] * 3)
    stt = MicSTT(FakeTranscriber("keep going that's fine"), mic, pause_frames=3, prompt="")

    assert stt.catch_stop(lambda: True) is False  # not a stop word, so it lets the reply run


def test_catch_stop_gives_up_the_moment_the_reply_finishes():
    ticks = {"n": 0}

    def still_speaking():
        ticks["n"] += 1
        return ticks["n"] < 3  # the reply ends after a couple of frames

    transcriber = FakeTranscriber("stop")
    stt = MicSTT(transcriber, FakeMic([_sil()] * 100), pause_frames=3, prompt="")

    assert stt.catch_stop(still_speaking) is False
    assert transcriber.got is None  # stopped watching without transcribing anything


def test_a_quiet_voice_on_a_quiet_mic_is_speech():
    # the deaf-mic bug: his voice peaked at 0.009 rms, under the old fixed 0.01 bar, so nothing
    # ever registered. Relative to his room's ~0.002 quiet, 0.009 is clearly speech.
    floor = NoiseFloor()
    assert floor.is_speech(0.002) is False  # first frame calibrates the room
    for _ in range(5):
        assert floor.is_speech(0.0025) is False  # ambient hovers near the floor
    assert floor.is_speech(0.009) is True  # his quiet voice clears the relative bar


def test_a_noisy_room_is_not_speech():
    # the opposite bug my gain patch caused: the room's noise (boosted to ~0.012) sat over the old
    # fixed bar, so it never saw a pause. Relative to a ~0.012 floor, 0.013 is just the room.
    floor = NoiseFloor()
    floor.is_speech(0.012)
    for _ in range(5):
        assert floor.is_speech(0.013) is False  # loud room, but not speech
    assert floor.is_speech(0.04) is True  # actual talking still cuts through


def test_the_floor_follows_the_room_back_down():
    floor = NoiseFloor()
    floor.is_speech(0.012)  # calibrated in a noisy moment
    for _ in range(60):
        floor.is_speech(0.002)  # the room settles
    assert floor.is_speech(0.009) is True  # the bar came down with it


def test_digital_silence_cannot_set_an_absurdly_low_bar():
    floor = NoiseFloor()
    floor.is_speech(0.0)
    for _ in range(20):
        floor.is_speech(0.0)  # a dead-quiet stream must not make any whisper of noise "speech"
    assert floor.is_speech(0.001) is False  # still below the clamped minimum bar


def test_listen_hears_a_quiet_voice_over_a_quiet_room_by_default():
    # end-to-end with the adaptive default (no fixed threshold): his real levels in miniature.
    ambient = [_sp(0.002)] * 6
    voice = [_sp(0.009)] * 4
    trailing = [_sp(0.002)] * 3
    stt = MicSTT(FakeTranscriber("hello over"), FakeMic(ambient + voice + trailing), pause_frames=3, prompt="")

    assert stt.listen() == "hello"


def test_pausing_after_over_ends_the_turn_and_fires_the_cue():
    fired = []
    mic = FakeMic([_sp()] * 5 + [_sil()] * 3)
    stt = MicSTT(FakeTranscriber("hello there over"), mic, pause_frames=3, prompt="", threshold=0.01, cue=lambda: fired.append(True))

    assert stt.listen() == "hello there"
    assert fired == [True]  # the "registered" cue fired the moment it caught the terminator


def test_a_thinking_pause_without_over_keeps_listening():
    # two speech bursts split by a mid-thought pause; each burst is transcribed on its own and the
    # pieces join, so the turn only ends when the LATEST piece carries the terminator.
    mic = FakeMic([_sp()] * 4 + [_sil()] * 3 + [_sp()] * 4 + [_sil()] * 3)
    transcriber = SeqTranscriber(["still thinking", "it over"])
    stt = MicSTT(transcriber, mic, pause_frames=3, prompt="", threshold=0.01)

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
    stt = MicSTT(MeasuringTranscriber(), mic, pause_frames=3, prompt="", threshold=0.01)
    stt.listen()

    assert sizes == [7 * FRAME, 8 * FRAME]  # NOT [7*FRAME, 15*FRAME] - the old buffer wasn't re-sent


def test_leading_silence_is_skipped():
    mic = FakeMic([_sil()] * 3 + [_sp()] * 4 + [_sil()] * 3)
    stt = MicSTT(FakeTranscriber("finally over"), mic, pause_frames=3, prompt="", threshold=0.01)

    assert stt.listen() == "finally"


def test_stream_end_returns_what_it_captured():
    # a real mic never ends, so nothing but "over" (or quitting) stops a turn; this only guards
    # the fallback for a finite source that runs out without a terminator.
    mic = FakeMic([_sp()] * 5)
    stt = MicSTT(FakeTranscriber("just some words"), mic, pause_frames=100, prompt="", threshold=0.01)

    assert stt.listen() == "just some words"


def test_listen_aborts_without_transcribing_when_stop_is_set():
    class Flag:
        def is_set(self):
            return True

    transcriber = FakeTranscriber("should not run over")
    stt = MicSTT(transcriber, FakeMic([_sp()] * 5), prompt="", stop=Flag())

    assert stt.listen() == ""


def test_a_lull_with_something_queued_yields_immediately_without_transcribing():
    interrupt = threading.Event()
    interrupt.set()  # the Entity has word from an agent to pass on, and he isn't talking
    transcriber = FakeTranscriber("should never run")
    stt = MicSTT(transcriber, FakeMic([_sil()] * 3), pause_frames=3, prompt="", threshold=0.01, interrupt=interrupt)

    assert stt.listen() == ""  # yields so the loop can speak the queued message
    assert transcriber.got is None  # nothing was captured or transcribed


def test_a_message_arriving_mid_sentence_does_not_cut_him_off():
    interrupt = threading.Event()

    class InterruptingMic:
        def frames(self):
            for _ in range(4):
                yield _sp()
            interrupt.set()  # word from an agent arrives, but he's already mid-sentence
            for _ in range(4):
                yield _sp()
            for _ in range(3):
                yield _sil()

    stt = MicSTT(
        FakeTranscriber("finishing my thought over"), InterruptingMic(),
        pause_frames=3, prompt="", threshold=0.01, interrupt=interrupt,
    )

    assert stt.listen() == "finishing my thought"  # he finished; the message waits its turn


def test_every_captured_frame_is_recorded_to_disk():
    written = []

    class Rec:
        def write(self, frame):
            written.append(frame)

    mic = FakeMic([_sp()] * 3 + [_sil()] * 3)
    stt = MicSTT(FakeTranscriber("hi over"), mic, pause_frames=3, prompt="", threshold=0.01, recorder=Rec())

    assert stt.listen() == "hi"
    assert len(written) == 6  # every frame read went to the recorder, before anything else
