"""The real microphone: a 16 kHz mono float32 input stream, read in fixed-size frames.

Hardware I/O only - the segmentation and transcription logic it feeds lives in `vad` and
`transcribe`, which are tested without a mic.
"""

import sounddevice as sd

SAMPLE_RATE = 16000
FRAME = 480  # 30 ms at 16 kHz


class Microphone:
    def __init__(self, *, samplerate=SAMPLE_RATE, blocksize=FRAME):
        self._blocksize = blocksize
        self._stream = sd.InputStream(samplerate=samplerate, channels=1, dtype="float32", blocksize=blocksize)
        self._stream.start()

    def read(self):
        data, _ = self._stream.read(self._blocksize)
        return data[:, 0].copy()

    def frames(self):
        while True:
            yield self.read()

    def close(self):
        self._stream.stop()
        self._stream.close()
