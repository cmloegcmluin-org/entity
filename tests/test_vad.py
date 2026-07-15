import numpy as np
import pytest

from entity.vad import VadSegmenter, calibrate_threshold, rms


def _speech(n=480, level=0.2):
    return np.full(n, level, dtype=np.float32)


def _silence(n=480):
    return np.zeros(n, dtype=np.float32)


def test_rms_of_silence_is_zero_and_speech_is_positive():
    assert rms(_silence()) == 0.0
    assert rms(_speech(level=0.2)) > 0.1


def test_calibrate_threshold_sits_above_the_ambient_floor():
    ambient = [_speech(level=0.02) for _ in range(5)]  # quiet room hum
    assert calibrate_threshold(ambient, floor=0.01, factor=3.0) == pytest.approx(0.06)


def test_calibrate_threshold_never_below_floor():
    assert calibrate_threshold([_silence()], floor=0.01) == 0.01


def test_segmenter_emits_utterance_after_trailing_silence():
    seg = VadSegmenter(threshold=0.05, silence_tail_frames=3, min_speech_frames=2)

    out = None
    for frame in [_silence(), _speech(), _speech(), _silence(), _silence(), _silence()]:
        result = seg.push(frame)
        if result is not None:
            out = result

    assert out is not None
    assert out.shape[0] == 480 * 5  # 2 speech + 3 trailing silence (pre-speech silence is dropped)


def test_segmenter_ignores_pure_silence():
    seg = VadSegmenter(threshold=0.05, silence_tail_frames=3, min_speech_frames=2)

    for frame in [_silence()] * 10:
        assert seg.push(frame) is None


def test_segmenter_discards_too_short_blip():
    seg = VadSegmenter(threshold=0.05, silence_tail_frames=3, min_speech_frames=3)

    out = None
    for frame in [_speech(), _silence(), _silence(), _silence()]:  # 1 speech frame < min 3
        result = seg.push(frame)
        if result is not None:
            out = result

    assert out is None
