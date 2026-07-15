"""Microphone speech-to-text with a walkie-talkie end-of-turn keyword.

You end a turn by saying a terminator word ("over") - not by going silent, and not on any time
limit. The end is only checked when you actually PAUSE: `listen()` captures once you start
talking, and each time you stop for a moment it transcribes what it has and looks for a trailing
"over". If it's there, the turn is done (and a cue fires so you see it registered); if not, you
just paused mid-thought and it keeps listening, however long you take. Saying "over" in the
middle of a sentence doesn't cut you off - there's no pause after it.
"""

import numpy as np

FRAME = 480  # 30 ms at 16 kHz
PAUSE_FRAMES = 17  # ~0.5 s of quiet = you paused, so check whether you said "over"
THRESHOLD = 0.01  # RMS above this counts as speech


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
        threshold=THRESHOLD,
        pause_frames=PAUSE_FRAMES,
        prompt="(listening... say 'over' when you're done)",
        stop=None,
        cue=None,
        recorder=None,
    ):
        self._transcriber = transcriber
        self._mic = mic
        self._terminator = terminator
        self._threshold = threshold
        self._pause_frames = pause_frames
        self._prompt = prompt
        self._stop = stop
        self._cue = cue
        self._recorder = recorder

    def listen(self):
        if self._prompt:
            print(self._prompt, flush=True)
        buffer = []
        silence_run = 0
        started = False
        for frame in self._mic.frames():
            if self._recorder is not None:
                self._recorder.write(frame)  # to disk first, so a crash can't lose what he said
            if self._stop is not None and self._stop.is_set():
                return ""  # a quit was requested while we were waiting for speech
            speech = rms(frame) >= self._threshold
            if not started:
                if speech:
                    started = True
                else:
                    continue
            buffer.append(frame)
            silence_run = 0 if speech else silence_run + 1
            if silence_run == self._pause_frames:  # you paused - did you say "over"?
                done = self._finish(buffer, forced=False)
                if done is not None:
                    return done
        return self._finish(buffer, forced=True) if buffer else ""

    def _finish(self, buffer, *, forced):
        """On a pause (or the stream ending), transcribe and return the turn if it ended with the
        terminator; on a plain mid-thought pause return None to keep listening."""
        text = self._transcriber.transcribe(np.concatenate(buffer))
        without_terminator = _strip_terminator(text, self._terminator)
        if without_terminator is not None:
            if self._cue is not None:
                self._cue()
            return without_terminator
        return text if forced else None
