import wave

import numpy as np

from excephalon.recorder import AudioRecorder


def test_recorder_writes_a_readable_wav(tmp_path):
    path = tmp_path / "sub" / "session.wav"  # nested dir is created
    recorder = AudioRecorder(path, samplerate=16000)

    loud = np.full(480, 0.5, dtype=np.float32)
    recorder.write(loud)
    recorder.write(np.zeros(480, dtype=np.float32))
    recorder.close()

    with wave.open(str(path), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getframerate() == 16000
        samples = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    assert len(samples) == 960
    assert abs(int(samples[0]) - round(0.5 * 32767)) <= 1  # first frame preserved


def test_data_is_on_disk_before_close(tmp_path):
    # the whole point: a crash before close() must not lose what was already spoken
    path = tmp_path / "session.wav"
    recorder = AudioRecorder(path)

    recorder.write(np.full(480, 0.3, dtype=np.float32))
    size_after_one_frame = path.stat().st_size  # flushed, so already sized on disk

    assert size_after_one_frame >= 480 * 2  # at least the frame's PCM bytes are already written
    recorder.close()
