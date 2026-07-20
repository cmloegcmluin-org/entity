import threading

import numpy as np

from entity.mic import BackgroundMicrophone
from entity.stt_mic import FRAME, MicSTT, NoiseFloor, _is_invented, _strip_terminator


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


def test_is_invented_spots_filler_but_keeps_real_words_and_the_terminator():
    assert _is_invented("Mm-hmm. Yeah. Uh.", "over") is True
    assert _is_invented("yeah", "over") is True
    assert _is_invented("Hey, I'm confused.", "over") is False  # real words, not filler
    assert _is_invented("yeah over", "over") is False  # carries the terminator - a real end
    assert _is_invented("", "over") is False


def test_okay_hallucinations_are_treated_as_backchannel():
    # Parakeet fills their pauses with "Okay." too; those must not pile up in front of their real turn.
    assert _is_invented("Okay.", "over") is True
    assert _is_invented("Okay okay okay", "over") is True
    assert _is_invented("Alright.", "over") is True
    assert _is_invented("okay do the thing", "over") is False  # real words survive


def test_the_models_stock_answer_to_silence_is_not_a_turn():
    # "Thank you." is what Parakeet returns for a stretch it can find no words in - five times in one
    # replayed 20-minute session, not once actually said. It's the same invention as "Okay." and
    # "Yeah.", which are already dropped; it just isn't a single word, so the word set can't see it.
    assert _is_invented("Thank you.", "over") is True
    assert _is_invented("Thanks!", "over") is True
    assert _is_invented("thank you for doing that", "over") is False  # a real sentence survives
    assert _is_invented("thank you over", "over") is False  # carries the terminator - a real end


def test_pure_backchannel_noise_is_dropped_from_the_turn():
    # a silent stretch that Parakeet hallucinates as "Mm-hmm." must not pollute the real turn.
    mic = FakeMic([_sp()] * 4 + [_sil()] * 3 + [_sp()] * 4 + [_sil()] * 3)
    transcriber = SeqTranscriber(["Mm-hmm.", "hello there over"])
    stt = MicSTT(transcriber, mic, pause_frames=3, threshold=0.01)

    assert stt.listen() == "hello there"  # the hallucinated chunk was dropped, only real speech kept


def test_a_burst_with_no_sustained_sound_never_reaches_the_transcriber():
    # A tap clears the speech bar and then the burst waits out a whole pause, so the model is handed
    # near silence and invents a word for it. Nothing spoken is that brief - don't even ask.
    mic = FakeMic([_sil()] * 2 + [_sp()] + [_sil()] * 3 + [_sp()] * 4 + [_sil()] * 3)
    transcriber = FakeTranscriber("hello there over")
    stt = MicSTT(transcriber, mic, pause_frames=3, threshold=0.01)

    assert stt.listen() == "hello there"
    assert len(transcriber.got) == 7 * FRAME  # the real burst - not the 4-frame tap ahead of it


def test_a_bare_over_ends_an_empty_turn_but_is_flagged_as_terminated():
    # they said only "over" - the transcript strips to nothing, but the terminator WAS caught, so the
    # caller can tell this apart from a lull and still let them know it registered.
    mic = FakeMic([_sp()] * 4 + [_sil()] * 3)
    stt = MicSTT(FakeTranscriber("over"), mic, pause_frames=3, threshold=0.01)

    assert stt.listen() == ""
    assert stt.caught_terminator is True


def test_an_empty_turn_without_a_terminator_is_not_flagged():
    # a lull yield (the Entity has queued news) returns "" with no terminator - must NOT look like "over".
    interrupt = threading.Event()
    interrupt.set()
    stt = MicSTT(FakeTranscriber("unused"), FakeMic([_sil()] * 3), pause_frames=3, threshold=0.01, interrupt=interrupt)

    assert stt.listen() == ""
    assert stt.caught_terminator is False


def test_a_normal_terminated_turn_is_flagged():
    mic = FakeMic([_sp()] * 4 + [_sil()] * 3)
    stt = MicSTT(FakeTranscriber("hello over"), mic, pause_frames=3, threshold=0.01)

    assert stt.listen() == "hello"
    assert stt.caught_terminator is True


class FlushableMic:
    """A mic that records the order of flush() and frame delivery, to prove listening starts fresh."""

    def __init__(self, frames):
        self._frames = list(frames)
        self.events = []

    def flush(self):
        self.events.append("flush")

    def frames(self):
        self.events.append("frames")
        while self._frames:
            yield self._frames.pop(0)


def test_listen_flushes_stale_audio_before_reading_this_turn():
    # between turns the background mic buffers the Entity's own reply and room noise; drop it before
    # a new listen so it isn't transcribed as their next turn.
    mic = FlushableMic([_sp()] * 4 + [_sil()] * 3)
    stt = MicSTT(FakeTranscriber("hi over"), mic, pause_frames=3, threshold=0.01)

    assert stt.listen() == "hi"
    assert mic.events[0] == "flush"  # flushed before the first frame was read


def test_catch_stop_flushes_stale_audio_before_watching():
    mic = FlushableMic([_sil()] * 2 + [_sp()] * 4 + [_sil()] * 3)
    stt = MicSTT(FakeTranscriber("keep going"), mic, pause_frames=3)

    stt.catch_stop(lambda: True)

    assert mic.events[0] == "flush"  # the stop-watcher, too, starts from now - not stale backlog


def test_listen_and_catch_stop_work_on_a_mic_without_flush():
    # ConsoleSTT-style / test mics have no flush(); listening must still work, unguarded getattr aside.
    mic = FakeMic([_sp()] * 4 + [_sil()] * 3)
    stt = MicSTT(FakeTranscriber("hello over"), mic, pause_frames=3, threshold=0.01)
    assert stt.listen() == "hello"


def test_catch_stop_fires_on_a_spoken_stop_word():
    mic = FakeMic([_sil()] * 2 + [_sp()] * 4 + [_sil()] * 3)  # quiet, a burst, then a pause
    stt = MicSTT(FakeTranscriber("Stop!"), mic, pause_frames=3)

    assert stt.catch_stop(lambda: True) is True  # they barked "stop" while it was talking


def test_catch_stop_ignores_ordinary_speech():
    mic = FakeMic([_sil()] * 2 + [_sp()] * 4 + [_sil()] * 3)
    stt = MicSTT(FakeTranscriber("keep going that's fine"), mic, pause_frames=3)

    assert stt.catch_stop(lambda: True) is False  # not a stop word, so it lets the reply run


def test_catch_stop_ignores_a_stop_word_buried_in_flowing_speech():
    # From the real session: the TV said "Wait, what do you do about it?" while the Entity was
    # speaking, and the buried "wait" silently killed the utterance - they were then told an offer
    # "on the screen only" that was in fact spoken and cut off at the first syllable. A deliberate
    # stop is a BARK; a stop word inside a sentence is the room, not them.
    mic = FakeMic([_sil()] * 2 + [_sp()] * 4 + [_sil()] * 3)
    stt = MicSTT(FakeTranscriber("Wait, what do you do about it?"), mic, pause_frames=3)

    assert stt.catch_stop(lambda: True) is False


def test_catch_stop_ignores_words_that_merely_contain_a_stop_word():
    mic = FakeMic([_sil()] * 2 + [_sp()] * 4 + [_sil()] * 3)
    stt = MicSTT(FakeTranscriber("the waiter stopped by"), mic, pause_frames=3)

    assert stt.catch_stop(lambda: True) is False  # "waiter"/"stopped" are not "wait"/"stop"


def test_catch_stop_still_fires_on_a_short_emphatic_bark():
    mic = FakeMic([_sil()] * 2 + [_sp()] * 4 + [_sil()] * 3)
    stt = MicSTT(FakeTranscriber("okay stop stop"), mic, pause_frames=3)

    assert stt.catch_stop(lambda: True) is True  # short and pointed - that's them, cutting in


def test_catch_stop_gives_up_the_moment_the_reply_finishes():
    ticks = {"n": 0}

    def still_speaking():
        ticks["n"] += 1
        return ticks["n"] < 3  # the reply ends after a couple of frames

    transcriber = FakeTranscriber("stop")
    stt = MicSTT(transcriber, FakeMic([_sil()] * 100), pause_frames=3)

    assert stt.catch_stop(still_speaking) is False
    assert transcriber.got is None  # stopped watching without transcribing anything


def test_a_quiet_voice_on_a_quiet_mic_is_speech():
    # the deaf-mic bug: their voice peaked at 0.009 rms, under the old fixed 0.01 bar, so nothing
    # ever registered. Relative to their room's ~0.002 quiet, 0.009 is clearly speech.
    floor = NoiseFloor()
    assert floor.is_speech(0.002) is False  # first frame calibrates the room
    for _ in range(5):
        assert floor.is_speech(0.0025) is False  # ambient hovers near the floor
    assert floor.is_speech(0.009) is True  # their quiet voice clears the relative bar


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


def test_a_steady_room_tone_over_a_stale_low_floor_settles_back_to_quiet():
    # The 16:30 session, replayed from its real audio: deep silence between their words dragged the
    # floor to its minimum, after which the room's ordinary tone (fan + PC hum, ~4x the stale floor)
    # read as endless "speech" - the pause never fired, "Stop listening. Over." was never absorbed,
    # and the session hung deaf inside the turn. A tone that IS the room now becomes the floor.
    floor = NoiseFloor()
    floor.is_speech(0.001)
    for _ in range(60):
        floor.is_speech(0.0008)  # deep silence between words - the floor ratchets to its minimum
    for _ in range(150):
        result = floor.is_speech(0.003)  # the room's steady tone, well over 2.5x the stale floor
    assert result is False  # the steady tone became the new quiet, so a pause can fire again
    assert floor.is_speech(0.009) is True  # their voice still clears the recalibrated bar


def test_real_speech_with_dips_does_not_drag_the_floor_up_to_itself():
    # The pull-up must never eat their voice: real talking always lets up somewhere within the
    # window, and that dip keeps the rolling minimum - and so the floor - down at the true quiet.
    floor = NoiseFloor()
    floor.is_speech(0.002)
    for _ in range(30):
        floor.is_speech(0.002)  # settled room
    for cycle in range(30):  # a long stretch of talking: bursts with brief inter-word dips
        for _ in range(20):
            assert floor.is_speech(0.03) is True  # loud speech stays speech throughout
        floor.is_speech(0.003)  # a between-words dip


def test_digital_silence_cannot_set_an_absurdly_low_bar():
    floor = NoiseFloor()
    floor.is_speech(0.0)
    for _ in range(20):
        floor.is_speech(0.0)  # a dead-quiet stream must not make any whisper of noise "speech"
    assert floor.is_speech(0.001) is False  # still below the clamped minimum bar


def test_listen_hears_a_quiet_voice_over_a_quiet_room_by_default():
    # end-to-end with the adaptive default (no fixed threshold): their real levels in miniature.
    ambient = [_sp(0.002)] * 6
    voice = [_sp(0.009)] * 4
    trailing = [_sp(0.002)] * 3
    stt = MicSTT(FakeTranscriber("hello over"), FakeMic(ambient + voice + trailing), pause_frames=3)

    assert stt.listen() == "hello"


def test_pausing_after_over_ends_the_turn_and_fires_the_cue():
    fired = []
    mic = FakeMic([_sp()] * 5 + [_sil()] * 3)
    stt = MicSTT(FakeTranscriber("hello there over"), mic, pause_frames=3, threshold=0.01, cue=lambda: fired.append(True))

    assert stt.listen() == "hello there"
    assert fired == [True]  # the "registered" cue fired the moment it caught the terminator


def test_a_thinking_pause_without_over_keeps_listening():
    # two speech bursts split by a mid-thought pause; each burst is transcribed on its own and the
    # pieces join, so the turn only ends when the LATEST piece carries the terminator.
    mic = FakeMic([_sp()] * 4 + [_sil()] * 3 + [_sp()] * 4 + [_sil()] * 3)
    transcriber = SeqTranscriber(["still thinking", "it over"])
    stt = MicSTT(transcriber, mic, pause_frames=3, threshold=0.01)

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
    stt = MicSTT(MeasuringTranscriber(), mic, pause_frames=3, threshold=0.01)
    stt.listen()

    assert sizes == [7 * FRAME, 8 * FRAME]  # NOT [7*FRAME, 15*FRAME] - the old buffer wasn't re-sent


def test_leading_silence_is_skipped():
    mic = FakeMic([_sil()] * 3 + [_sp()] * 4 + [_sil()] * 3)
    stt = MicSTT(FakeTranscriber("finally over"), mic, pause_frames=3, threshold=0.01)

    assert stt.listen() == "finally"


def test_stream_end_returns_what_it_captured():
    # a real mic never ends, so nothing but "over" (or quitting) stops a turn; this only guards
    # the fallback for a finite source that runs out without a terminator.
    mic = FakeMic([_sp()] * 5)
    stt = MicSTT(FakeTranscriber("just some words"), mic, pause_frames=100, threshold=0.01)

    assert stt.listen() == "just some words"


def test_listen_aborts_without_transcribing_when_stop_is_set():
    class Flag:
        def is_set(self):
            return True

    transcriber = FakeTranscriber("should not run over")
    stt = MicSTT(transcriber, FakeMic([_sp()] * 5), stop=Flag())

    assert stt.listen() == ""


def test_a_lull_with_something_queued_yields_immediately_without_transcribing():
    interrupt = threading.Event()
    interrupt.set()  # the Entity has word from an agent to pass on, and they aren't talking
    transcriber = FakeTranscriber("should never run")
    stt = MicSTT(transcriber, FakeMic([_sil()] * 3), pause_frames=3, threshold=0.01, interrupt=interrupt)

    assert stt.listen() == ""  # yields so the loop can speak the queued message
    assert transcriber.got is None  # nothing was captured or transcribed


def test_a_message_arriving_mid_sentence_does_not_cut_the_user_off():
    interrupt = threading.Event()

    class InterruptingMic:
        def frames(self):
            for _ in range(4):
                yield _sp()
            interrupt.set()  # word from an agent arrives, but they're already mid-sentence
            for _ in range(4):
                yield _sp()
            for _ in range(3):
                yield _sil()

    stt = MicSTT(
        FakeTranscriber("finishing my thought over"), InterruptingMic(),
        pause_frames=3, threshold=0.01, interrupt=interrupt,
    )

    assert stt.listen() == "finishing my thought"  # they finished; the message waits its turn


class GatedSource:
    """A frame source that yields nothing until release() - so a test can hold every frame back
    until listen() has already flushed, making the background-capture composition race-free."""

    def __init__(self, frames):
        self._frames = list(frames)
        self._go = threading.Event()

    def read(self):
        self._go.wait()
        if not self._frames:
            raise EOFError
        return self._frames.pop(0)

    def release(self):
        self._go.set()

    def close(self):
        self._go.set()


class FlushSignallingMic:
    """Delegates to a real BackgroundMicrophone but announces when flush() lands, so the test knows
    listening has started before it releases any audio."""

    def __init__(self, inner, flushed):
        self._inner = inner
        self._flushed = flushed

    def flush(self):
        self._inner.flush()
        self._flushed.set()

    def frames(self):
        return self._inner.frames()


def test_micstt_drives_a_real_background_microphone_end_to_end():
    # the production path: MicSTT reading through a background-capture mic. Frames are gated until
    # after listen() flushes, so what it transcribes is exactly the audio captured during the turn.
    flushed = threading.Event()
    source = GatedSource([_sp()] * 4 + [_sil()] * 3)
    background = BackgroundMicrophone(source)
    stt = MicSTT(FakeTranscriber("hello there over"), FlushSignallingMic(background, flushed),
                 pause_frames=3, threshold=0.01)

    heard = {}
    turn = threading.Thread(target=lambda: heard.__setitem__("text", stt.listen()))
    turn.start()
    assert flushed.wait(timeout=2)  # listen() has flushed; only now do frames start flowing
    source.release()
    turn.join(timeout=3)

    assert heard["text"] == "hello there"
    assert stt.caught_terminator is True
    background.close()


def test_every_captured_frame_is_recorded_to_disk():
    written = []

    class Rec:
        def write(self, frame):
            written.append(frame)

    mic = FakeMic([_sp()] * 4 + [_sil()] * 3)
    stt = MicSTT(FakeTranscriber("hi over"), mic, pause_frames=3, threshold=0.01, recorder=Rec())

    assert stt.listen() == "hi"
    assert len(written) == 7  # every frame read went to the recorder, before anything else
