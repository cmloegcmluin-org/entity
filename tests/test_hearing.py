import numpy as np

from entity.hearing import Hearing, settled
from entity.stt_mic import Burst


class FakeTranscriber:
    """Hands out the scripted readings, and remembers how much audio each one was given."""

    def __init__(self, *texts):
        self._texts = list(texts)
        self.given = []

    def transcribe(self, audio):
        self.given.append(len(audio))
        return self._texts.pop(0) if self._texts else ""


def _burst(frames):
    burst = Burst()
    for _ in range(frames):
        burst.add(np.zeros(480, dtype=np.float32), speech=True, level=0.05)
    return burst


def test_only_the_words_two_readings_agree_on_are_settled():
    # Measured on their own session audio: the tail of a partial reading is guesswork the next
    # reading rewrites ("I need to say" -> "I need to set up"), while the head stays put. Showing
    # only the agreed head is what makes the line grow instead of flickering.
    older, newer = "Then tell me exactly what I need", "Then tell me exactly what I need to set up"

    assert settled(older, newer) == "Then tell me exactly what I need"


def test_a_word_the_model_recased_or_repunctuated_still_counts_as_agreed():
    # Replayed from their own audio: "in the hungry Newman work tree," came back as "In the Hungry
    # Newman Work Tree on" - the same words, differently dressed. Compared strictly, the line
    # stalls at the first comma the model changes its mind about and never catches up.
    older, newer = "In the hungry Newman work tree,", "in the Hungry Newman Work Tree on"

    # And the fresher spelling is the one shown - it is the model's latest reading, not its first.
    assert settled(older, newer) == "in the Hungry Newman Work Tree"


def test_the_line_only_ever_grows_however_the_readings_wobble():
    # The readings taken while they were still talking, off their own captured audio. Handed a stretch
    # it cannot place, Parakeet answers with NOTHING at all - four times in this one sentence. A
    # line that emptied and refilled four times in three seconds is unreadable, so it never shrinks.
    hearing = Hearing(None, lambda text: None)

    assert hearing.hear("In the") == ""  # one reading agrees with nothing; nothing is settled yet
    assert hearing.hear("In the hungry") == "In the"
    assert hearing.hear("") == "In the"
    assert hearing.hear("In the hungry Newman?") == "In the"
    assert hearing.hear("In the hungry Newman working") == "In the hungry Newman"


def test_the_line_is_reported_when_it_grows_and_comes_down_when_the_burst_ends():
    # It grows three times a second while they talk, so an unchanged line must not be re-sent - the
    # window would redraw it on every poll. And the burst ending is what puts the finished sentence
    # in the draft box, so the live line has to clear or they read the same words twice.
    shown = []
    hearing = Hearing(None, shown.append)

    hearing.hear("Remember to")
    hearing.hear("Remember to pop")
    hearing.hear("")  # a reading that settles nothing leaves the line where it is, unannounced
    hearing.rest()

    assert shown == ["Remember to", ""]
    assert hearing.hear("Something else entirely") == ""  # the next burst starts from nothing


def test_only_the_newest_snapshot_is_read_so_the_pump_never_falls_behind():
    # Reading a growing buffer costs 90 ms at one second of speech and 640 ms at twenty (measured
    # on their own audio, this machine). Done on the pump's thread it would fall a second behind per
    # second of talking, so snapshots pile up in one slot and the newest wins - the reading rate
    # drops on a long sentence instead of the mic drifting out of real time.
    transcriber = FakeTranscriber("Remember to", "Remember to pop open")
    hearing = Hearing(transcriber, lambda text: None, every=4)

    hearing.follow(_burst(4))  # a reading is due...
    hearing.follow(_burst(8))  # ...and another before the first was taken

    assert hearing.step() is True
    assert transcriber.given == [8 * 480]  # the stale one was dropped, not queued
    assert hearing.step() is False  # nothing waiting: the worker sleeps rather than spinning


def test_a_reading_interrupted_by_a_pause_is_not_carried_into_the_next_burst():
    # The worker is part-way through a reading when they stop talking. Its answer describes a burst
    # nobody is in any more, so leaving it standing as "the reading before this one" would have the
    # NEXT burst agree with it - and the first thing they saw of a new sentence would be words from
    # the last one, which is the kind of thing this app has put in their draft box before.
    class ReadInterruptedByHisPause:
        def __init__(self):
            self.hearing = None

        def transcribe(self, audio):
            self.hearing.rest()
            return "In the hungry Newman"

    shown = []
    transcriber = ReadInterruptedByHisPause()
    hearing = Hearing(transcriber, shown.append, every=4)
    transcriber.hearing = hearing

    hearing.follow(_burst(4))
    hearing.step()

    assert shown == []  # that reading never reached the screen at all
    assert hearing.hear("In the hungry Newman work tree") == ""  # the next burst starts from nothing


def test_a_line_that_was_never_up_does_not_come_down():
    # Every pause ends a burst, including the ones nobody was listening to - the room while the mic
    # is off, the Entity's own voice. A line that is not on screen has nothing to take off it, and
    # saying so anyway crosses the feed into the window for no reason at all.
    shown = []
    hearing = Hearing(None, shown.append)

    hearing.rest()

    assert shown == []
