"""Local speech-to-text with NVIDIA Parakeet via onnx-asr (using onnx-asr's shared model cache).

`recognize()` takes a float32 mono 16 kHz numpy array (straight from the mic) and returns
the transcript text. The 2.4 GB model loads once, lazily, on first use (~3s).

`CorrectingTranscriber` wraps any transcriber to bias its output toward the user's own vocabulary -
the names they coined and the domain terms of their fields (see `vocabulary`). Since Parakeet has
no hotword hook, the bias is applied after recognition.
"""

from entity.vocabulary import correct_terms, translations_in_force

DEFAULT_MODEL = "nemo-parakeet-tdt-0.6b-v3"


def _load_parakeet(name):
    import onnx_asr

    # CPU by design: it runs faster than realtime, and onnxruntime's CoreML path fails to
    # initialize this external-data model.
    return onnx_asr.load_model(name, providers=["CPUExecutionProvider"])


class ParakeetTranscriber:
    def __init__(self, *, model=None, loader=_load_parakeet, model_name=DEFAULT_MODEL):
        self._model = model
        self._loader = loader
        self._model_name = model_name

    def _get_model(self):
        if self._model is None:
            self._model = self._loader(self._model_name)
        return self._model

    def transcribe(self, audio):
        return self._get_model().recognize(audio).strip()

    def warmup(self):
        """Load the model now (at startup) so the first spoken turn isn't the one that waits."""
        self._get_model()


class CorrectingTranscriber:
    """Wraps a transcriber and rewrites its output toward known terms, so Parakeet's "note craft"
    comes back as "Notecraft" and "bayesan inference" as "Bayesian inference". Transparent
    otherwise: same `transcribe`/`warmup` surface, so it drops in wherever a plain transcriber goes.

    `translations` are the named ones - "cloud agent" for "Claude agent" - which no similarity
    score can catch because what came back is ordinary English. Their own are merged over the ones
    that ship, so a rule they write for a phrase wins."""

    def __init__(self, transcriber, terms, *, translations=None, threshold=None):
        self._transcriber = transcriber
        self._terms = list(terms)
        self.translations = translations_in_force(translations)
        # None -> defer to correct_terms' tuned default, so the threshold lives in exactly one place.
        self._kwargs = {} if threshold is None else {"threshold": threshold}

    def transcribe(self, audio):
        return correct_terms(self._transcriber.transcribe(audio), self._terms,
                             translations=self.translations, **self._kwargs)

    def retune(self, *, translations=None, terms=None):
        """Swap what is in force NOW: an edit on the Config page corrects the very next chunk,
        instead of waiting for the next launch."""
        if translations is not None:
            self.translations = translations_in_force(translations)
        if terms is not None:
            self._terms = list(terms)

    def warmup(self):
        self._transcriber.warmup()
