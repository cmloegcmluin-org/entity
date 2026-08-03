import threading

import numpy as np

from excephalon.voiceprint import Scorekeeper, Voiceprint, speechy_chunks


def _wait_for(check, timeout=2.0):
    tick = threading.Event()
    for _ in range(int(timeout / 0.01)):
        if check():
            return True
        tick.wait(0.01)
    return check()


def _tone(seconds=2.0, level=0.1):
    return np.full(int(16000 * seconds), level, dtype=np.float32)


def _embedder_by_loudness(samples):
    """A fake fingerprint that depends only on how loud the audio is - loud and quiet audio get
    far-apart vectors, so tests can stage same-voice and different-voice without a real model."""
    loud = float(np.sqrt(np.mean(samples**2)) > 0.05)
    return np.array([loud, 1.0 - loud, 0.5])


def test_speechy_chunks_keep_sound_and_drop_silence():
    audio = np.concatenate([_tone(2.0, 0.1), np.zeros(int(16000 * 2)), _tone(2.0, 0.1)])

    kept = list(speechy_chunks(audio.astype(np.float32)))

    assert len(kept) == 2  # the silent middle window holds no speech worth fingerprinting


def test_enrolling_saves_a_print_and_scoring_recognizes_the_same_voice(tmp_path):
    print_of = Voiceprint(tmp_path, embedder=_embedder_by_loudness)

    saved = print_of.enroll(_tone(seconds=10.0, level=0.1))

    assert saved.exists()
    assert print_of.score(_tone(level=0.1)) > 0.99   # the same "voice" (loud) matches
    assert print_of.score(_tone(level=0.02)) < 0.75  # a different "voice" (quiet) does not


def test_a_fresh_start_reads_the_print_back_from_disk(tmp_path):
    Voiceprint(tmp_path, embedder=_embedder_by_loudness).enroll(_tone(seconds=10.0))

    reopened = Voiceprint(tmp_path, embedder=_embedder_by_loudness)

    assert reopened.score(_tone()) is not None


def test_without_an_enrollment_there_is_no_score(tmp_path):
    # No fingerprint on file means no opinion - never a guess. The caller treats None as
    # "keep the words": failing toward hearing him is the whole design.
    print_of = Voiceprint(tmp_path, embedder=_embedder_by_loudness)

    assert print_of.score(_tone()) is None


def test_the_scorekeeper_writes_each_heard_chunk_with_its_score(tmp_path):
    # Watch-only: every chunk the mic turned into words gets its against-his-voice score written
    # beside the words, and nothing anywhere DECIDES on it - the log is the evidence the dropping
    # threshold will one day be chosen from, across real sessions.
    voiceprint = Voiceprint(tmp_path, embedder=_embedder_by_loudness)
    voiceprint.enroll(_tone(seconds=10.0, level=0.1))
    keeper = Scorekeeper(tmp_path, voiceprint=voiceprint, clock=lambda spec: "12-00-00")

    keeper.note(_tone(level=0.1), "his words, as transcribed")

    logged = tmp_path / "scores-12-00-00.log"
    assert _wait_for(logged.exists)
    line = logged.read_text(encoding="utf-8")
    assert "1.00" in line and "his words, as transcribed" in line


def test_without_a_learned_voice_the_scorekeeper_stays_silent(tmp_path):
    keeper = Scorekeeper(tmp_path, clock=lambda spec: "12-00-00")

    keeper.note(_tone(), "anything")

    settle = threading.Event()
    settle.wait(0.1)
    assert list(tmp_path.glob("scores-*.log")) == []


def test_enrolling_on_pure_silence_is_refused(tmp_path):
    # A minute of a dead mic must not become the fingerprint "silence": every later sound would
    # score against noise, and the one previous try at voice-filtering went deaf exactly by
    # trusting thin evidence.
    print_of = Voiceprint(tmp_path, embedder=_embedder_by_loudness)

    try:
        print_of.enroll(np.zeros(int(16000 * 10), dtype=np.float32))
    except ValueError as refusal:
        assert "speech" in str(refusal)
    else:
        raise AssertionError("an all-silence enrollment was accepted")
