import threading
import time

import numpy as np

from entity.dictation import Dictation

LOUD = 0.05
QUIET = 0.001


def _sp(level=LOUD):
    return np.full(480, level, dtype=np.float32)


def _sil():
    return _sp(QUIET)


class FakeMic:
    def __init__(self, frames):
        self._frames = list(frames)

    def frames(self):
        yield from self._frames


class FakeTranscriber:
    """Hands out the scripted texts, one per transcribed chunk."""

    def __init__(self, *texts):
        self._texts = list(texts)
        self.calls = 0

    def transcribe(self, audio):
        self.calls += 1
        return self._texts.pop(0) if self._texts else ""


class Ears:
    """Collects everything the dictation reports, the way the window would."""

    def __init__(self):
        self.drafted = []
        self.states = []
        self.levels = []
        self.submits = 0

    def kwargs(self):
        return dict(
            on_draft=self.drafted.append,
            on_state=self.states.append,
            on_level=self.levels.append,
            on_submit_request=self._submit,
        )

    def _submit(self):
        self.submits += 1


def _burst_then_pause():
    # Quiet first: the floor calibrates on the opening frame, so a stream that STARTS loud would
    # set the bar at voice level and hear nothing at all.
    return [_sil()] * 2 + [_sp()] * 4 + [_sil()] * 4


def test_speech_while_recording_lands_in_the_draft():
    ears = Ears()
    dictation = Dictation(FakeTranscriber("add eggs to the list"), FakeMic(_burst_then_pause()),
                          pause_frames=3, **ears.kwargs())

    dictation.pump()

    assert ears.drafted == ["add eggs to the list"]
    assert ears.submits == 0  # nothing submitted - the draft just accumulates


def test_stop_listening_mutes_and_keeps_the_words_before_it():
    ears = Ears()
    dictation = Dictation(FakeTranscriber("add eggs, stop listening", "invisible while muted"),
                          FakeMic(_burst_then_pause() * 2), pause_frames=3, **ears.kwargs())

    dictation.pump()

    assert ears.drafted == ["add eggs"]  # the phrase (and its comma) never lands in the draft
    assert ears.states[-1] == "muted"  # and the mic went off


def test_muted_speech_is_dropped_until_hey_entity():
    ears = Ears()
    dictation = Dictation(
        FakeTranscriber("just the TV talking", "hey entity add milk", "and bread"),
        FakeMic(_burst_then_pause() * 3), pause_frames=3, muted=True, **ears.kwargs(),
    )

    dictation.pump()

    assert ears.drafted == ["add milk", "and bread"]  # the wake phrase carried its first words
    assert ears.states[-1] == "recording"


def test_over_still_submits_for_the_old_muscle_memory():
    ears = Ears()
    dictation = Dictation(FakeTranscriber("send the report over"),
                          FakeMic(_burst_then_pause()), pause_frames=3, **ears.kwargs())

    dictation.pump()

    assert ears.drafted == ["send the report"]
    assert ears.submits == 1


def test_over_carries_a_short_answer_through_instead_of_reading_it_as_filler():
    # "Yeah, over" is the exact case the backchannel filter is written to let through - it refuses
    # to call anything filler if the terminator is in it. Stripping the terminator FIRST and then
    # asking took that protection away: the answer was dropped, the submit found an empty draft
    # box, and saying "over" did nothing whatsoever. Half his answers are one of these words.
    ears = Ears()
    dictation = Dictation(FakeTranscriber("Yeah, over."), FakeMic(_burst_then_pause()),
                          pause_frames=3, **ears.kwargs())

    dictation.pump()

    assert ears.drafted == ["Yeah,"]
    assert ears.submits == 1


def test_over_ends_the_recording_as_well_as_submitting():
    # Both halves of what he asked for: "over" is the whole gesture for "I'm done talking", so it
    # hands the turn over AND puts the mic down, rather than leaving it live on the room.
    ears = Ears()
    dictation = Dictation(FakeTranscriber("send the report over"), FakeMic(_burst_then_pause()),
                          pause_frames=3, **ears.kwargs())

    dictation.pump()

    assert ears.submits == 1
    assert ears.states[-1] == "muted"


def test_the_level_meter_sees_the_mic_only_while_recording():
    ears = Ears()
    dictation = Dictation(FakeTranscriber(""), FakeMic([_sp(0.04), _sp(0.04)]),
                          pause_frames=3, muted=True, **ears.kwargs())

    dictation.pump()

    assert ears.levels == [0.0, 0.0]  # muted: the meter shows nothing, whatever the room does

    ears2 = Ears()
    Dictation(FakeTranscriber(""), FakeMic([_sp(0.04)]), pause_frames=3, **ears2.kwargs()).pump()

    assert ears2.levels and ears2.levels[0] > 0.01  # recording: the real level


def test_a_burst_with_no_sustained_sound_is_never_even_transcribed():
    # Replayed from his own session audio: a single tap or creak clears the speech bar, the burst
    # then has to wait out a whole pause before it ends, and Parakeet - handed a second of near
    # silence - answers with the likeliest thing anyone ever says ("Thank you.", "Okay."). Some 90
    # times in 20 minutes. Nothing a person says is that brief, so the burst never goes to the model.
    ears = Ears()
    transcriber = FakeTranscriber("Thank you.")
    dictation = Dictation(transcriber, FakeMic([_sil()] * 2 + [_sp()] + [_sil()] * 4),
                          pause_frames=3, **ears.kwargs())

    dictation.pump()

    assert ears.drafted == []
    assert transcriber.calls == 0  # not transcribed-then-dropped: never asked in the first place


def test_hallucinated_backchannel_chunks_stay_out_of_the_draft():
    ears = Ears()
    dictation = Dictation(FakeTranscriber("Mm-hmm. Yeah."), FakeMic(_burst_then_pause()),
                          pause_frames=3, **ears.kwargs())

    dictation.pump()

    assert ears.drafted == []


def test_the_button_toggle_flips_state_and_reports_it():
    ears = Ears()
    dictation = Dictation(FakeTranscriber(), FakeMic([]), **ears.kwargs())

    dictation.set_recording(False)
    dictation.set_recording(True)

    assert ears.states == ["muted", "recording"]


def test_listen_hands_back_what_the_window_submits():
    ears = Ears()
    dictation = Dictation(FakeTranscriber(), FakeMic([]), **ears.kwargs())
    heard = {}

    def listener():
        heard["text"] = dictation.listen()

    thread = threading.Thread(target=listener)
    thread.start()
    dictation.submit("the edited draft, as they corrected it")
    thread.join(2.0)

    assert heard["text"] == "the edited draft, as they corrected it"


def test_listen_yields_empty_when_interrupted_so_agent_news_can_speak():
    interrupt = threading.Event()
    ears = Ears()
    dictation = Dictation(FakeTranscriber(), FakeMic([]), interrupt=interrupt, **ears.kwargs())
    heard = {}

    def listener():
        heard["text"] = dictation.listen()

    thread = threading.Thread(target=listener)
    thread.start()
    interrupt.set()
    thread.join(2.0)

    assert heard["text"] == ""  # a lull broken for the outbox, not a real turn


def test_a_stop_event_ends_the_pump_mid_stream():
    stop = threading.Event()
    stop.set()
    ears = Ears()
    dictation = Dictation(FakeTranscriber("never"), FakeMic([_sp()] * 50), stop=stop,
                          pause_frames=3, **ears.kwargs())

    dictation.pump()  # returns promptly instead of consuming the stream

    assert ears.drafted == []


def test_catch_stop_hears_a_bark_and_keeps_it_out_of_the_draft():
    ears = Ears()
    dictation = Dictation(FakeTranscriber("stop"), FakeMic(_burst_then_pause() + [_sil()] * 20),
                          pause_frames=3, **ears.kwargs())
    caught = {}

    def watcher():
        caught["stopped"] = dictation.catch_stop(lambda: caught.get("stopped") is None)

    thread = threading.Thread(target=watcher, daemon=True)  # daemon: a hang can't wedge the suite
    thread.start()
    deadline = time.monotonic() + 2.0
    while dictation._bark is None and time.monotonic() < deadline:
        time.sleep(0.005)  # the pump must not outrun the watcher installing its bark event
    dictation.pump()
    thread.join(2.0)

    assert caught.get("stopped") is True  # the bark cut the voice
    assert ears.drafted == []  # and never became draft text


def test_every_frame_reaches_the_recorder_even_while_muted():
    # The crash-proof audio capture must not depend on the mic state - their words are only
    # recoverable if they were written before anything else happened to them.
    written = []

    class FakeRecorder:
        def write(self, frame):
            written.append(frame)

    ears = Ears()
    dictation = Dictation(FakeTranscriber(""), FakeMic([_sil(), _sp(), _sil()]),
                          pause_frames=3, muted=True, recorder=FakeRecorder(), **ears.kwargs())

    dictation.pump()

    assert len(written) == 3


def test_nothing_is_drafted_while_the_entity_is_speaking():
    # Their draft box opened with "I do for you" - the tail of Entity's own spoken greeting, heard
    # through their speakers. Its own voice must never become their words.
    ears = Ears()
    dictation = Dictation(FakeTranscriber("I'm ready. What can I do for you?"),
                          FakeMic(_burst_then_pause()), pause_frames=3, **ears.kwargs())
    dictation.begin_speaking()

    dictation.pump()

    assert ears.drafted == []
    assert ears.states[-1] == "speaking"  # and the window can say so on its button
    assert ears.levels[-1] == 0.0  # the meter shows nothing: it isn't listening to them


def test_when_it_stops_speaking_the_mic_returns_to_how_he_left_it():
    ears = Ears()
    dictation = Dictation(FakeTranscriber(), FakeMic([]), **ears.kwargs())

    dictation.begin_speaking()
    dictation.end_speaking()

    assert ears.states == ["speaking", "recording"]  # they were recording before, so they still are


def test_cutting_it_off_leaves_the_mic_off_rather_than_recording_the_next_breath():
    # "stopping shouldn't immediately turn on record".
    ears = Ears()
    dictation = Dictation(FakeTranscriber(), FakeMic([]), **ears.kwargs())

    dictation.begin_speaking()
    dictation.set_recording(False)  # what the button does when it's showing STOP
    dictation.end_speaking()

    assert ears.states[-1] == "muted"


def test_a_muted_mic_stays_muted_through_a_reply():
    ears = Ears()
    dictation = Dictation(FakeTranscriber(), FakeMic([]), muted=True, **ears.kwargs())

    dictation.begin_speaking()
    dictation.end_speaking()

    assert ears.states[-1] == "muted"


def test_starting_the_pump_announces_the_state_it_was_built_in():
    # The window opens before the mic exists, so it has to be told - otherwise a mic that starts
    # off is drawn as listening until something happens to change it.
    ears = Ears()
    dictation = Dictation(FakeTranscriber(), FakeMic([]), muted=True, **ears.kwargs())

    dictation.start().join(2.0)

    assert ears.states[0] == "muted"


def test_turning_the_mic_off_keeps_the_sentence_he_had_just_finished_saying():
    # They spoke a whole sentence, then hit mic-off, and the words never appeared: the burst was
    # still buffered, waiting for a pause that muting made irrelevant. Muting is not "forget the
    # part you hadn't transcribed yet".
    held = []

    class MicHePressesMuteDuring:
        """They are mid-sentence when they reach for the button - the burst has started and no pause
        has ended it yet."""

        def frames(self):
            for index, frame in enumerate([_sil()] * 2 + [_sp()] * 6):
                if index == 5:
                    held[0].set_recording(False)
                yield frame

    ears = Ears()
    dictation = Dictation(FakeTranscriber("the whole sentence I just said"),
                          MicHePressesMuteDuring(), pause_frames=3, **ears.kwargs())
    held.append(dictation)

    dictation.pump()

    assert ears.drafted == ["the whole sentence I just said"]
    assert ears.states[-1] == "muted"  # and it did go quiet, as they asked


def test_it_reports_whether_they_are_part_way_through_a_sentence():
    # The loop asks this before ever speaking up on its own. Being ARMED must not read as talking:
    # they leave the mic armed for a whole conversation, and taking that for "they are speaking" left
    # the Entity unable to say anything unprompted for the entire session.
    ears = Ears()
    held = []
    seen = []

    class MicThatWatches:
        def frames(self):
            for frame in _burst_then_pause():
                seen.append(held[0].is_mid_utterance())
                yield frame

    dictation = Dictation(FakeTranscriber("a sentence"), MicThatWatches(),
                          pause_frames=3, **ears.kwargs())
    held.append(dictation)

    assert dictation.is_mid_utterance() is False  # armed from the start, but they haven't spoken yet

    dictation.pump()

    assert True in seen  # it did say so while a burst was still in the air
    assert dictation.is_mid_utterance() is False  # and stopped once they paused - mic still armed


def test_the_sounds_he_makes_while_thinking_never_reach_the_draft():
    ears = Ears()
    dictation = Dictation(FakeTranscriber("Um, so uh the drive link is wrong"),
                          FakeMic(_burst_then_pause()), pause_frames=3, **ears.kwargs())

    dictation.pump()

    assert ears.drafted == ["So the drive link is wrong"]
