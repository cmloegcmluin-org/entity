"""What he is saying, while he is still saying it.

The mic only ever printed a sentence once he had finished it, because a burst is transcribed when
a pause ends it - so the wait he saw was the pause itself, plus the reading. To print words as
they arrive, the burst so far has to be read over and over while it grows.

Parakeet cannot be fed audio a piece at a time: `recognize` takes a waveform and reads all of it.
So each reading re-reads the whole burst from its start. Measured on his own captured sessions on
this machine: 90 ms at one second of speech, 150 ms at five, 280 ms at ten, 640 ms at twenty -
around thirty times faster than real time, which is what makes re-reading affordable at all. It is
still far too slow to sit on the pump's thread, where it would put the mic a second behind per
second he talked, so the readings happen on a worker and the pump only ever leaves a snapshot.

The readings themselves are not fit to show. Replayed over his own audio, the tail of every one of
them is guesswork the next reading rewrites - "I need to say" became "I need to set up in Google
Cloud" - and four times in one three-second sentence the model answered a stretch it could not
place with nothing at all. Printed straight, the line would empty and refill while he read it.
What is trustworthy is the part two readings in a row AGREE on: across 123 readings of ten real
bursts, the agreed head grew word by word and went backwards three times, twice only in casing or
a comma. So a word is shown once it has been heard the same way twice, and the line never shrinks.
"""

import threading
import time

from entity.phrases import canonical

READ_EVERY = 10  # frames of new audio between readings - 300 ms, comfortably above one reading
IDLE = 0.02      # how long the worker sleeps with nothing to read


def settled(older, newer):
    """The words two successive readings of the same audio agree on, from the start.

    Compared as words, not as spelling: the model recases and repunctuates freely while it is
    still deciding ("in the hungry Newman work tree," -> "In the Hungry Newman Work Tree on"), and
    counting that as disagreement stalls the line at the first comma it changes its mind about.
    The newer spelling is what comes back - it is the model's latest reading of those words."""
    old_words, new_words = older.split(), newer.split()
    kept = 0
    for old, new in zip(old_words, new_words):
        if canonical(old) != canonical(new):
            break
        kept += 1
    return " ".join(new_words[:kept])


class Hearing:
    """The live line: what he has said so far in the burst he is still speaking."""

    def __init__(self, transcriber, on_hearing, *, every=READ_EVERY, idle=IDLE):
        self._transcriber = transcriber
        self._on_hearing = on_hearing
        self._every = every
        self._idle = idle
        self._previous = ""  # the reading before this one, which this one has to agree with
        self._line = ""      # what is on screen, which only ever grows
        self._lock = threading.Lock()
        self._waiting = None  # the one snapshot the worker will read next, newest wins
        self._burst = 0       # which burst that snapshot belongs to
        self._running = True

    def hear(self, hypothesis):
        """Take one reading of the burst so far and return the line it leaves on screen.

        Reported only when it changes: the readings come three times a second, and re-sending the
        same words would have the window redraw the line on every poll."""
        agreed = settled(self._previous, hypothesis)
        self._previous = hypothesis
        if len(agreed.split()) > len(self._line.split()):
            self._line = agreed
            self._on_hearing(self._line)
        return self._line

    def rest(self):
        """He paused: the burst is over. Its finished text goes in the draft box, so the live line
        comes down rather than standing there saying the same thing twice."""
        with self._lock:
            self._burst += 1  # a reading still in the air belongs to a burst nobody is in now
            self._waiting = None
        self._previous = self._line = ""
        self._on_hearing("")

    # ---- the worker ----------------------------------------------------------------------------

    def follow(self, burst):
        """Called by the pump for every frame; leaves a snapshot to read when one is due.

        One slot, not a queue: while a reading is in progress the pump keeps arriving, and the
        only snapshot worth having then is the newest. Stale ones are dropped, which is what makes
        the reading rate - rather than the mic - give way on a long sentence."""
        if not len(burst) or len(burst) % self._every:
            return
        with self._lock:
            self._waiting = (self._burst, burst.audio())

    def step(self):
        """Read the waiting snapshot, if there is one. True if there was. The worker loop is this
        in a while; a test calls it directly."""
        with self._lock:
            waiting, self._waiting = self._waiting, None
        if waiting is None:
            return False
        burst, audio = waiting
        hypothesis = self._transcriber.transcribe(audio)
        with self._lock:
            stale = burst != self._burst  # he stopped talking while this was being read
        if not stale:
            self.hear(hypothesis)
        return True

    def work(self):
        while self._running:
            if not self.step():
                time.sleep(self._idle)

    def start(self):
        thread = threading.Thread(target=self.work, daemon=True)
        thread.start()
        return thread

    def close(self):
        self._running = False
