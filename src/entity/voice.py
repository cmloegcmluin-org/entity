"""How a streamed reply becomes audible speech, sentence by sentence.

The old voice spoke a reply only once the whole of it existed, which put the entire brain latency
between the question and the first sound. Here the reply's text arrives as deltas while the model
is still writing, a sentence is cut the moment its end appears, and each finished sentence is
synthesized and played while the next is still forming - so the wait to first words is the time to
the first sentence END, not to the end of the whole turn.
"""

import queue
import re
import threading

# The seam a spoken sentence ends on: closing punctuation followed by whitespace (or a newline,
# which ends a line of a list the same way). A period right after a digit is an enumerator
# ("1. Open the console") - the number belongs to its step, so it is no seam. Abbreviations
# ("e.g. ") occasionally split early; a spurious beat between two fragments costs less than
# holding real sentences back.
_SENTENCE_END = re.compile(r"(?<=[.!?:])(?<![0-9]\.)\s+|\n+")


class SentenceStream:
    """Cuts a stream of text deltas into sentences, handing each over the moment it is complete."""

    def __init__(self, on_sentence):
        self._on_sentence = on_sentence
        self._pending = ""

    def feed(self, delta):
        self._pending += delta
        parts = _SENTENCE_END.split(self._pending)
        for done in parts[:-1]:
            if done.strip():
                self._on_sentence(done.strip())
        self._pending = parts[-1]

    def flush(self):
        """The reply is over: whatever is still pending is its last words, spoken as they stand."""
        tail, self._pending = self._pending.strip(), ""
        if tail:
            self._on_sentence(tail)


_END = object()  # closes a Reply's queue; never spoken


class Speaker:
    """The voice: one engine, one way audio goes out, for one-shot lines and streamed replies alike.

    `engine.say(text) -> (samples, samplerate)` synthesizes; `play(samples, samplerate, interrupt)`
    makes it audible and returns early when the interrupt fires. Both are injected, so every
    behavior here is tested without an audio device."""

    def __init__(self, engine, *, play):
        self._engine = engine
        self._play = play

    def speak(self, text, *, interrupt=None):
        """One whole utterance - a greeting, a piece of news. The same contract SystemTTS had."""
        said = str(text).strip()
        if not said or _fired(interrupt):
            return
        samples, samplerate = self._engine.say(said)
        self._play(samples, samplerate, interrupt=interrupt)

    def stream(self, *, interrupt=None):
        """A reply about to arrive as text deltas: speak it sentence by sentence as it forms."""
        return Reply(self._engine, self._play, interrupt)


class Reply:
    """One streamed reply being spoken while it is still being written.

    Deltas go in on whatever thread the brain streams from; a worker of its own synthesizes and
    plays each finished sentence, so the next sentence forms while the last one sounds. `done()`
    waits for the audio to run out and returns what was actually spoken - which, after a barge-in,
    is the head of the reply that got out before the cut."""

    def __init__(self, engine, play, interrupt):
        self._engine = engine
        self._play = play
        self._interrupt = interrupt
        self._queue = queue.SimpleQueue()
        self._spoken = []
        self._sentences = SentenceStream(self._queue.put)
        self._worker = threading.Thread(target=self._pump, daemon=True)
        self._worker.start()

    def add(self, delta):
        self._sentences.feed(delta)

    def done(self):
        """The reply's text is complete; wait out the audio and say what of it was spoken."""
        self._sentences.flush()
        self._queue.put(_END)
        self._worker.join()
        return " ".join(self._spoken)

    def _pump(self):
        while True:
            sentence = self._queue.get()
            if sentence is _END:
                return
            if _fired(self._interrupt):
                continue  # cut off: drain the rest unspoken, so done() comes straight back
            samples, samplerate = self._engine.say(sentence)
            if _fired(self._interrupt):
                continue
            self._play(samples, samplerate, interrupt=self._interrupt)
            self._spoken.append(sentence)  # heard - at least its start, if the cut came mid-word


def _fired(interrupt):
    return interrupt is not None and interrupt.is_set()


def _sounddevice_stream(samplerate):
    import sounddevice

    return sounddevice.OutputStream(samplerate=samplerate, channels=1, dtype="float32")


def play_samples(samples, samplerate, *, interrupt=None, open_stream=_sounddevice_stream,
                 chunk_seconds=0.1):
    """Make a synthesized clip audible, in pieces small enough that a cut-off feels instant.

    The interrupt is checked between writes, so a piece is the longest a reply can keep sounding
    after they say stop - a tenth of a second, not the rest of the sentence."""
    step = max(1, int(samplerate * chunk_seconds))
    with open_stream(samplerate) as stream:
        for start in range(0, len(samples), step):
            if _fired(interrupt):
                return
            stream.write(samples[start:start + step])
