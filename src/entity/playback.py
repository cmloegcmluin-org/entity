"""What this PC is sending to its own speakers, so the mic can discount exactly that.

A stream playing in Chrome reaches the mic as ordinary speech: sustained, loud, well-formed
sentences. Nothing about its shape says it isn't the user - one session had a card-game streamer's
commentary landing in the draft box word for word. The only thing that reliably tells the two apart
is that one of them is also coming out of the speakers.

So the machine's own output is captured alongside the mic, and a burst is discounted when its
loudness FOLLOWS the playback's. Loudness, not the waveform: the room mangles phase and frequency
beyond recognition, but the envelope - loud here, quiet there - survives the trip through the air
intact. Correlating envelopes also means the gate does not care how loud the speakers are set.

Measured on the real thing, a stream playing and the user talking over it with the volume "on full,
loud enough that it's distracting me": the speaker-to-mic delay was 90 ms, bursts that were the
stream matched at r = 0.38 to 0.96, and his own bursts at -0.26 to +0.58. A 0.6 bar drops four
fifths of the stream and none of him. Plain envelope correlation beat both log and sqrt envelopes on
that data - log put one of HIS bursts at 0.74, above half the stream's.
"""

import threading
import time

import numpy as np

LAG_SECONDS = 0.09  # speaker -> air -> mic, measured; re-measure with a paired capture on new hardware
ECHO = 0.6  # how closely a burst has to follow the playback to be the machine
AUDIBLE = 0.002  # below this the speakers are effectively off and there is nothing to discount
KEEP_SECONDS = 30.0  # how much playback history to hold, comfortably longer than any one burst


def echoes_playback(heard, played, *, threshold=ECHO, audible=AUDIBLE):
    """Did this burst's loudness follow what the speakers were putting out?

    `heard` and `played` are the per-frame levels of one burst and of the playback over the same
    stretch, already delayed by LAG_SECONDS. Silence in `played` means the machine was quiet, so
    whatever the mic heard was the room - never the machine - however the numbers happen to line up.
    """
    heard, played = np.asarray(heard, dtype=np.float64), np.asarray(played, dtype=np.float64)
    if len(heard) != len(played) or len(heard) < 3:
        return False
    if float(np.mean(played)) < audible:
        return False
    return _correlation(heard, played) >= threshold


def _correlation(a, b):
    a, b = a - a.mean(), b - b.mean()
    spread = np.sqrt(np.dot(a, a) * np.dot(b, b))
    return 0.0 if spread < 1e-12 else float(np.dot(a, b) / spread)


class LoopbackSource:
    """The machine's own output, read back as per-frame loudness. Windows/WASAPI, via `soundcard`.

    The audio stack the mic uses cannot do this: its PortAudio build exposes no loopback flag and no
    loopback devices, so a second library earns its place here. Only levels leave this class - the
    playback's actual audio is never kept, which is worth saying out loud about a thing that can hear
    everything the machine plays.

    The DEFAULT output is what gets captured. Windows moves the default when headphones go in and
    apps follow it, so tracking it tracks him; audio an app has been routed to some other device by
    hand is not captured, and is not discounted.
    """

    def __init__(self, *, frame=480, rate=16000, retry_seconds=2.0):
        import soundcard  # here, and eagerly: a machine without it must fail where that is reported

        self._soundcard = soundcard
        self._frame = frame
        self._rate = rate
        self._retry = retry_seconds
        self._running = True

    def levels(self):
        while self._running:
            try:
                speaker = self._soundcard.default_speaker()
                loopback = self._soundcard.get_microphone(id=str(speaker.name), include_loopback=True)
                with loopback.recorder(samplerate=self._rate, channels=1) as recorder:
                    while self._running:
                        block = recorder.record(numframes=self._frame).reshape(-1)
                        yield float(np.sqrt(np.mean(block * block)))
            except Exception:
                # The default output changes when headphones go in, and the old handle dies with it.
                # Losing the capture must never take the mic down with it - just pick it up again.
                time.sleep(self._retry)

    def close(self):
        self._running = False


class Playback:
    """A rolling record of how loud this PC's output has been, timestamped.

    Timestamped rather than counted, because the mic and the speakers are two devices with two
    clocks: an index-based lookup drifts further out of step the longer a session runs, while
    "how loud were the speakers 90 ms ago" stays true all day.
    """

    def __init__(self, source, *, lag=LAG_SECONDS, keep=KEEP_SECONDS, clock=time.monotonic):
        self._source = source
        self._lag = lag
        self._keep = keep
        self._clock = clock
        self._history = []  # (when, level), oldest first
        self._lock = threading.Lock()
        self._running = True

    def note(self, level, when=None):
        """Record one frame's output level. Called by the capture loop; a test calls it directly."""
        when = self._clock() if when is None else when
        with self._lock:
            self._history.append((when, level))
            cutoff = when - self._keep
            if self._history[0][0] < cutoff:
                self._history = [pair for pair in self._history if pair[0] >= cutoff]

    def level(self, when=None):
        """How loud the speakers were `lag` ago - i.e. what should be reaching the mic right now."""
        wanted = (self._clock() if when is None else when) - self._lag
        with self._lock:
            if not self._history:
                return 0.0
            nearest = min(self._history, key=lambda pair: abs(pair[0] - wanted))
        return nearest[1]

    def pump(self):
        """Read the machine's output forever, recording how loud it is. Its own thread; a finite
        test source just runs out."""
        for level in self._source.levels():
            if not self._running:
                return
            self.note(level)

    def start(self):
        thread = threading.Thread(target=self.pump, daemon=True)
        thread.start()
        return thread

    def close(self):
        self._running = False
