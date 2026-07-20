import numpy as np

from entity.playback import Playback, echoes_playback


def test_a_burst_that_follows_what_the_speakers_played_is_the_machine():
    # Measured on his own hardware, a stream playing and him talking over it at full volume: bursts
    # that were the stream matched the delay-aligned playback at r = 0.38-0.96, and his own bursts
    # at -0.26 to +0.58. The shape is what tells them apart, not the loudness.
    played = np.array([0.02, 0.05, 0.06, 0.03, 0.05, 0.07, 0.02, 0.01])
    through_the_room = played * 0.12  # the same shape, quieter, once it has been through the air

    assert echoes_playback(through_the_room, played) is True


def test_a_voice_over_the_playback_is_kept():
    # He talks while the stream keeps going, so the mic carries both - and his own shape is nothing
    # like the playback's. This is the failure that matters: dropping him makes the app deaf.
    played = np.array([0.02, 0.05, 0.06, 0.03, 0.05, 0.07, 0.02, 0.01])
    him_over_it = np.array([0.01, 0.01, 0.02, 0.03, 0.02, 0.01, 0.01, 0.00]) + played * 0.05

    assert echoes_playback(him_over_it, played) is False


def test_nothing_is_discounted_while_the_speakers_are_silent():
    # With the machine quiet there is nothing to subtract, so a burst is his however the numbers
    # happen to line up - two near-silent series correlate on their own rounding noise otherwise.
    quiet = np.array([0.00002, 0.00005, 0.00006, 0.00003, 0.00005, 0.00007, 0.00002, 0.00001])

    assert echoes_playback(quiet * 40, quiet) is False


def test_the_level_it_reports_is_the_one_now_arriving_at_the_mic():
    # What left the speakers 90 ms ago is what the mic is picking up now, so that is what a burst
    # has to be compared against - asked at 1.09 it must answer with 1.00's level, not 1.09's.
    now = [1.00]
    playback = Playback(source=None, lag=0.09, clock=lambda: now[0])
    for level in (0.01, 0.02, 0.03, 0.04, 0.05):
        playback.note(level)
        now[0] += 0.03

    assert playback.level(1.09) == 0.01  # 1.09 - 0.09 = 1.00, and 1.00 is where 0.01 went out
    assert playback.level(1.12) == 0.02  # a frame later, the one that went out at 1.03


def test_it_forgets_playback_older_than_it_will_ever_be_asked_about():
    # A session runs for hours; the history must not grow with it.
    now = [0.0]
    playback = Playback(source=None, keep=1.0, clock=lambda: now[0])
    for _ in range(200):
        playback.note(0.05)
        now[0] += 0.03

    assert len(playback._history) < 40  # ~1 s at 30 ms a frame, not the 200 frames it was fed
