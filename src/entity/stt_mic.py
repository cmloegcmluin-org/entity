"""Microphone speech-to-text: listen for one spoken utterance, transcribe it, return the text.

`listen()` reads mic frames through the VAD segmenter until a full utterance closes (you stop
talking), then hands that audio to the transcriber. The mic and segmenter are injected so the
loop is testable without hardware.
"""

from entity.vad import VadSegmenter, calibrate_threshold


class MicSTT:
    def __init__(self, transcriber, mic, *, segmenter=None, calibration_frames=15, prompt="(listening...)"):
        self._transcriber = transcriber
        self._mic = mic
        self._prompt = prompt
        if segmenter is None:
            ambient = [mic.read() for _ in range(calibration_frames)]
            segmenter = VadSegmenter(threshold=calibrate_threshold(ambient))
        self._segmenter = segmenter

    def listen(self):
        if self._prompt:
            print(self._prompt, flush=True)
        for frame in self._mic.frames():
            utterance = self._segmenter.push(frame)
            if utterance is not None:
                return self._transcriber.transcribe(utterance)
        return ""
