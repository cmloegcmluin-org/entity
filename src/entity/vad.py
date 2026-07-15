"""Energy-based voice activity detection: slice a stream of mic frames into utterances.

Deliberately dependency-free (just numpy) - a frame counts as speech when its RMS energy
clears a threshold, an utterance starts on the first speech frame and ends once enough
trailing silence follows. `calibrate_threshold` sets that threshold above the room's noise
floor so it adapts to the mic and environment.
"""

import numpy as np


def rms(frame):
    frame = np.asarray(frame, dtype=np.float32)
    if frame.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(frame * frame)))


def calibrate_threshold(ambient_frames, *, floor=0.01, factor=3.0):
    """A speech threshold that sits a factor above the loudest ambient (silence) frame."""
    levels = [rms(frame) for frame in ambient_frames]
    ambient = max(levels) if levels else 0.0
    return max(floor, ambient * factor)


class VadSegmenter:
    def __init__(self, *, threshold=0.02, silence_tail_frames=25, min_speech_frames=5):
        self._threshold = threshold
        self._silence_tail = silence_tail_frames
        self._min_speech = min_speech_frames
        self._collecting = []
        self._speech_frames = 0
        self._silence_run = 0
        self._started = False

    def push(self, frame):
        """Feed one frame; return the completed utterance (concatenated frames) or None."""
        if rms(frame) >= self._threshold:
            self._started = True
            self._speech_frames += 1
            self._silence_run = 0
            self._collecting.append(frame)
        elif self._started:
            self._silence_run += 1
            self._collecting.append(frame)
            if self._silence_run >= self._silence_tail:
                return self._finish()
        return None

    def _finish(self):
        frames = self._collecting
        enough_speech = self._speech_frames >= self._min_speech
        self._collecting = []
        self._speech_frames = 0
        self._silence_run = 0
        self._started = False
        if not enough_speech:
            return None
        return np.concatenate(frames)
