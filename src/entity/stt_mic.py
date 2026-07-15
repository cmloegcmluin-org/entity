"""Microphone speech-to-text with a walkie-talkie end-of-turn keyword.

You end a turn by saying a terminator word ("over") - not by going silent. Guessing the end
of a turn from silence was unreliable (it either cut the user off or never fired in a noisy
room), so instead `listen()` keeps capturing once you start talking, re-transcribes every
second or so, and returns the moment the transcript ends with "over" (which is stripped off).
A max length is the safety net if you forget to say it.
"""

import numpy as np

FRAME = 480  # 30 ms at 16 kHz
CHECK_EVERY = 33  # ~1 s: how often to transcribe-and-check for the terminator
MAX_FRAMES = 666  # ~20 s: send the turn even if "over" is never said
START_THRESHOLD = 0.01  # RMS above this = you've started talking


def rms(frame):
    frame = np.asarray(frame, dtype=np.float32)
    if frame.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(frame * frame)))


def _strip_terminator(text, terminator):
    """Return the text minus a trailing terminator word, or None if it isn't there."""
    words = text.split()
    if words and words[-1].lower().strip(".,!?;:'\"") == terminator:
        return " ".join(words[:-1]).strip()
    return None


class MicSTT:
    def __init__(
        self,
        transcriber,
        mic,
        *,
        terminator="over",
        start_threshold=START_THRESHOLD,
        check_every=CHECK_EVERY,
        max_frames=MAX_FRAMES,
        prompt="(listening... say 'over' when you're done)",
        stop=None,
    ):
        self._transcriber = transcriber
        self._mic = mic
        self._terminator = terminator
        self._start_threshold = start_threshold
        self._check_every = check_every
        self._max_frames = max_frames
        self._prompt = prompt
        self._stop = stop

    def listen(self):
        if self._prompt:
            print(self._prompt, flush=True)
        buffer = []
        since_check = 0
        started = False
        for frame in self._mic.frames():
            if self._stop is not None and self._stop.is_set():
                return ""  # a quit was requested while we were waiting for speech
            if not started:
                if rms(frame) >= self._start_threshold:
                    started = True
                else:
                    continue
            buffer.append(frame)
            since_check += 1
            forced = len(buffer) >= self._max_frames
            if since_check >= self._check_every or forced:
                since_check = 0
                result = self._transcribe(buffer, forced=forced)
                if result is not None:
                    return result
        return self._transcribe(buffer, forced=True) if buffer else ""

    def _transcribe(self, buffer, *, forced):
        """Transcribe the buffer; return text minus a trailing 'over', or the raw text if we're
        forced to end (max length / stream ended), or None to keep listening."""
        text = self._transcriber.transcribe(np.concatenate(buffer))
        without_terminator = _strip_terminator(text, self._terminator)
        if without_terminator is not None:
            return without_terminator
        return text if forced else None
