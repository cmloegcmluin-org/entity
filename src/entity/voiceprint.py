"""How Excephalon learns whose voice it should listen for, and how it asks later "is this them?".

One minute of the user reading anything aloud becomes a numerical fingerprint of their voice: the
recording is cut into short windows that actually hold sound, each window is embedded by a small
speaker-recognition model, and the average of those embeddings is saved - in `runtime/voice/`,
beside the model that computed it, because a voice is personal and nothing personal enters the
repo. Scoring compares any scrap of audio against the saved fingerprint (cosine similarity).

What to DO about a score is deliberately not decided here. The one previous attempt at
hearing-only-the-user went deaf to them - a threshold fitted to a single recording, armed at
once - so this module only measures. The cutoff between "theirs" and "not theirs" is chosen
later, from scores logged across real sessions, and until that evidence exists a missing
fingerprint or model yields None: no opinion, and the caller keeps the words.

Run `python -m entity.voiceprint` (or double-click "Learn my voice.bat") to record the minute
and save the fingerprint.
"""

import queue
import threading
import time
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
MODEL_FILE = "wespeaker_en_voxceleb_CAM++.onnx"
PRINT_FILE = "voiceprint.npz"
RECORDING_FILE = "enrollment.wav"  # the raw minute, kept so a future model can re-learn from it

# Windows this long carry enough voice for the model to fingerprint; the spike measured 2 s as
# plenty. The floor is the same energy line the spike used to tell sound from the room.
CHUNK_SECONDS = 2.0
ENERGY_FLOOR = 0.008


def speechy_chunks(samples, seconds=CHUNK_SECONDS, floor=ENERGY_FLOOR):
    """Fixed windows of the audio that actually hold sound - RMS above the floor."""
    step = int(SAMPLE_RATE * seconds)
    return [
        window
        for start in range(0, len(samples) - step + 1, step)
        if np.sqrt(np.mean((window := samples[start:start + step]) ** 2)) >= floor
    ]


class Voiceprint:
    """The saved fingerprint of the user's voice, and the scoring of audio against it."""

    def __init__(self, directory, *, embedder=None):
        self._dir = Path(directory)
        self._embedder = embedder  # samples -> vector; None means build the real model on use
        self._print = None

    def enroll(self, samples):
        """Learn the voice from one recording: fingerprint its speechy windows, average, save.
        Returns the saved path. Refused outright when the recording holds almost no speech -
        a fingerprint of silence would score everything against noise."""
        chunks = speechy_chunks(np.asarray(samples, dtype=np.float32))
        if len(chunks) < 3:
            raise ValueError("the recording holds almost no speech - nothing to learn a voice from")
        vectors = [self._embed(chunk) for chunk in chunks]
        self._print = np.mean(vectors, axis=0)
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / PRINT_FILE
        np.savez(path, print=self._print)
        return path

    def score(self, samples):
        """How much this audio sounds like the enrolled voice: cosine similarity, or None when
        there is no fingerprint (or no model) to ask - no opinion, never a guess."""
        known = self._known()
        if known is None:
            return None
        try:
            heard = self._embed(np.asarray(samples, dtype=np.float32))
        except Exception:
            return None  # a scoring failure must never decide anything about his words
        return float(np.dot(known, heard) / (np.linalg.norm(known) * np.linalg.norm(heard)))

    def _known(self):
        if self._print is None:
            try:
                self._print = np.load(self._dir / PRINT_FILE)["print"]
            except (OSError, KeyError, ValueError):
                return None
        return self._print

    def _embed(self, samples):
        if self._embedder is None:
            self._embedder = _sherpa_embedder(self._dir / MODEL_FILE)
        return self._embedder(samples)


class Scorekeeper:
    """Watch-only: scores what the mic heard against the learned voice, writes each score beside
    the words it came with, and decides nothing.

    The log is the evidence a dropping threshold will one day be chosen from - across real
    sessions, with the user's actual words on the keep side - because the one previous attempt
    picked its threshold from a single recording and went deaf to them. Scoring runs on a worker
    of its own: the mic's pump thread hands a chunk over and moves on, so measuring can never
    slow the draft. Without a learned voice on file there is nothing to measure against, and
    nothing is written at all."""

    def __init__(self, directory, *, voiceprint=None, clock=time.strftime):
        self._dir = Path(directory)
        self._voiceprint = voiceprint or Voiceprint(directory)
        self._clock = clock
        self._chunks = queue.SimpleQueue()
        self._path = None  # named on first write, so an unused session leaves no empty file
        self._worker = None

    def note(self, audio, text):
        """Queue one heard chunk for scoring. Returns at once; the line lands when the worker
        gets to it."""
        if not (self._dir / PRINT_FILE).exists():
            return  # no voice has been learned; there is no evidence to collect yet
        if self._worker is None:
            self._worker = threading.Thread(target=self._keep, daemon=True)
            self._worker.start()
        self._chunks.put((np.asarray(audio, dtype=np.float32), text))

    def _keep(self):
        while True:
            audio, text = self._chunks.get()
            score = self._voiceprint.score(audio)
            if score is None:
                continue  # the model failed on this chunk; a gap in the log, never a guess
            if self._path is None:
                self._path = self._dir / f"scores-{self._clock('%Y%m%d-%H%M%S')}.log"
            with open(self._path, "a", encoding="utf-8") as log:
                log.write(f"{score:.2f}  {text}\n")


def _sherpa_embedder(model_path):
    """The real model, built only when first asked for: the import and the file stay out of
    every path that doesn't score audio, tests included."""
    import sherpa_onnx

    extractor = sherpa_onnx.SpeakerEmbeddingExtractor(
        sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(model_path), num_threads=2)
    )

    def embed(samples):
        stream = extractor.create_stream()
        stream.accept_waveform(sample_rate=SAMPLE_RATE, waveform=samples)
        stream.input_finished()
        return np.array(extractor.compute(stream))

    return embed


def _record_and_learn():  # pragma: no cover - a microphone and a person, not a test's business
    """The double-click flow: record one minute of them reading, keep the wav, save the print."""
    import wave

    import sounddevice as sd

    runtime_voice = Path(__file__).resolve().parents[2] / "runtime" / "voice"
    seconds = 60
    print("Excephalon is going to learn your voice.")
    print(f"When the countdown starts, read anything aloud - out of your head or off a page -")
    print(f"for {seconds} seconds. Keep the room otherwise quiet: no music, no TV, nobody else.")
    input("Press Enter when you're ready to start... ")
    print("Recording - keep reading.")
    taken = sd.rec(int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="float32")
    for remaining in range(seconds, 0, -10):
        print(f"  {remaining} seconds left...")
        sd.sleep(10 * 1000)
    sd.wait()
    samples = taken.reshape(-1)

    runtime_voice.mkdir(parents=True, exist_ok=True)
    with wave.open(str(runtime_voice / RECORDING_FILE), "wb") as keeper:
        keeper.setnchannels(1)
        keeper.setsampwidth(2)
        keeper.setframerate(SAMPLE_RATE)
        keeper.writeframes((samples * 32767).astype(np.int16).tobytes())

    voiceprint = Voiceprint(runtime_voice)
    try:
        voiceprint.enroll(samples)
    except ValueError:
        print("\nThat recording held almost no speech - the mic may not have picked you up.")
        print("Nothing was saved. Run this again, a little closer to the mic.")
        return
    held_out = speechy_chunks(samples)[-5:]
    scores = [voiceprint.score(chunk) for chunk in held_out]
    print(f"\nLearned. Your own voice scores {min(scores):.2f}-{max(scores):.2f} against the")
    print("fingerprint (1.00 is a perfect match) - Excephalon can now start measuring what is and")
    print("isn't you. Nothing changes in how it hears until those measurements prove safe.")


if __name__ == "__main__":  # pragma: no cover
    _record_and_learn()
