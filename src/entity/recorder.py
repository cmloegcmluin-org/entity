"""Continuously write mic frames to a WAV on disk, so nothing the user says is ever lost.

Every frame is flushed to disk the instant it's captured. If the process crashes mid-turn (it
has, losing minutes of his ideas), the audio is already on disk - recoverable even if the WAV
header never got finalized (the raw 16-bit PCM sits right after the 44-byte header).
"""

import wave
from pathlib import Path

import numpy as np


class AudioRecorder:
    def __init__(self, path, samplerate=16000):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self._file = open(path, "wb")
        self._wav = wave.open(self._file, "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(samplerate)

    def write(self, frame):
        pcm = np.clip(np.asarray(frame, dtype=np.float32), -1.0, 1.0)
        self._wav.writeframes((pcm * 32767).astype("<i2").tobytes())
        self._file.flush()  # onto disk every frame - a crash then loses nothing

    def close(self):
        try:
            self._wav.close()  # finalizes the header; does not close the underlying file object
        finally:
            self._file.close()
