"""Microphone speech-to-text with a walkie-talkie end-of-turn keyword.

You end a turn by saying a terminator word ("over") - not by going silent, and not on any time
limit. The end is only checked when you actually PAUSE: `listen()` captures once you start
talking, and each time you stop for a moment it transcribes just the new stretch since your last
pause, tacks it onto the running transcript, and looks for a trailing "over". If it's there, the
turn is done (and a cue fires so you see it registered); if not, you just paused mid-thought and
it keeps listening, however long you take. Because each pause only transcribes the newest chunk -
never the whole turn again - the cost of a pause stays flat no matter how long you've been
talking, so a long turn can't slow to a crawl or run the machine out of memory. Saying "over" in
the middle of a sentence doesn't cut you off - there's no pause after it.

What counts as "speech" is judged against the ROOM, not a fixed number. A fixed loudness bar
failed both ways in practice: a quiet mic put the user's voice just under it (deaf), and boosting
the mic put the room's noise just over it (never a pause, also deaf). NoiseFloor keeps a running
measure of the room's quiet level; anything a few times louder is speech. That stays right
whatever the mic's level is.
"""

import numpy as np

FRAME = 480  # 30 ms at 16 kHz
PAUSE_FRAMES = 17  # ~0.5 s of quiet = you paused, so check whether you said "over"
SPEECH_RATIO = 2.5  # this many times the room's quiet level counts as speech
FLOOR_MIN = 0.0008  # the floor never drops below this, so digital silence can't set an absurd bar
FLOOR_ADAPT = 0.1  # how fast the floor tracks quiet frames (EMA step)


def rms(frame):
    frame = np.asarray(frame, dtype=np.float32)
    if frame.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(frame * frame)))


class NoiseFloor:
    """The room's running quiet level, learned from the frames that aren't speech.

    The first frame calibrates it (never counted as speech - at that instant there's nothing to
    compare against). After that, a frame is speech if it's SPEECH_RATIO times the floor; every
    non-speech frame nudges the floor toward its level, so the bar follows the room - up when a fan
    kicks in, down when things settle - and never assumes anything about the mic's absolute level.
    """

    def __init__(self, ratio=SPEECH_RATIO, adapt=FLOOR_ADAPT, floor_min=FLOOR_MIN):
        self._ratio = ratio
        self._adapt = adapt
        self._floor_min = floor_min
        self._level = None

    def is_speech(self, level):
        if self._level is None:
            self._level = max(level, self._floor_min)
            return False
        if level >= self._level * self._ratio:
            return True
        self._level = max(self._level + (level - self._level) * self._adapt, self._floor_min)
        return False


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
        threshold=None,
        pause_frames=PAUSE_FRAMES,
        prompt="(listening... say 'over' when you're done)",
        stop=None,
        cue=None,
        recorder=None,
        interrupt=None,
    ):
        self._transcriber = transcriber
        self._mic = mic
        self._terminator = terminator
        # A fixed threshold is for tests and odd setups; live use adapts to the room instead.
        self._is_speech = (lambda level: level >= threshold) if threshold is not None else NoiseFloor().is_speech
        self._pause_frames = pause_frames
        self._prompt = prompt
        self._stop = stop
        self._cue = cue
        self._recorder = recorder
        self._interrupt = interrupt

    def listen(self):
        if self._prompt:
            print(self._prompt, flush=True)
        segments = []  # transcribed text so far, one entry per pause-delimited chunk
        segment = []  # frames captured since the last pause - only this chunk gets transcribed
        silence_run = 0
        started = False
        for frame in self._mic.frames():
            if self._recorder is not None:
                self._recorder.write(frame)  # to disk first, so a crash can't lose what he said
            if self._stop is not None and self._stop.is_set():
                return ""  # a quit was requested while we were waiting for speech
            speech = self._is_speech(rms(frame))
            if not started:
                if self._interrupt is not None and self._interrupt.is_set():
                    return ""  # a lull, and the Entity has something to say - yield so it can
                if speech:
                    started = True
                else:
                    continue
            segment.append(frame)
            silence_run = 0 if speech else silence_run + 1
            if silence_run == self._pause_frames:  # you paused - did you say "over"?
                done = self._absorb(segments, segment)
                segment = []  # this chunk is now text; the next one starts fresh
                if done is not None:
                    return done
        if segment:  # a finite source ran out mid-chunk (a real mic never does)
            done = self._absorb(segments, segment)
            if done is not None:
                return done
        return " ".join(segments).strip()

    def _absorb(self, segments, chunk):
        """Transcribe one pause-delimited chunk, append it to the running transcript, and return
        the finished turn if the transcript now ends with the terminator - else None to keep
        listening. Only this chunk is transcribed, never the whole turn, so the work per pause
        stays flat however long the turn runs."""
        text = self._transcriber.transcribe(np.concatenate(chunk)).strip()
        if text:
            segments.append(text)
        without_terminator = _strip_terminator(" ".join(segments), self._terminator)
        if without_terminator is not None:
            if self._cue is not None:
                self._cue()
            return without_terminator
        return None
