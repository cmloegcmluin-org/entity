"""Custom vocabulary: bias transcription toward the user's own coined names.

Parakeet has no idea what "Notecraft", "WaveShaper" or "Skylark" are, so it renders them as whatever
ordinary words sound closest ("high ideas", "gina"). onnx-asr's Parakeet path exposes no hotword /
contextual-biasing hook (its RecognizeOptions cover only Whisper/Canary language flags), so the
bias happens AFTER transcription: `correct_terms` swaps any near-miss token for the closest known
term. The term list is pluggable - `scan_terms` reads it off his filesystem, but tests inject a
plain list, so none of this needs a real disk.
"""

import difflib
import re
from pathlib import Path

_SPLIT = re.compile(r"^(\W*)(.*?)(\W*)$")  # leading punctuation, the bare word, trailing punctuation
_SEPARATORS = re.compile(r"[ _\-]+")

# Generic folder names that are ordinary English (or infrastructure) and so aren't worth biasing
# toward - and, being common words, would invite false corrections of normal speech.
DEFAULT_STOPWORDS = frozenset({
    "shared", "vision", "pytorch", "python", "core", "common", "src", "lib", "libs",
    "test", "tests", "temp", "tmp", "data", "assets", "build", "dist", "node_modules",
    "venv", "env", "archive", "backup", "old", "new", "misc", "projects", "workspace",
    "documents", "downloads", "desktop", "scripts", "utils", "vendor",
})


def _normalize(name):
    """The spoken/written form of a directory name: "wave_shaper" -> "WaveShaper", "notecraft" -> "Notecraft".
    A name that already carries its own capitalisation (ComfyUIApp) keeps it, minus separators."""
    parts = [p for p in _SEPARATORS.split(name.strip()) if p]
    if any(c.isupper() for c in name):
        return "".join(parts)
    return "".join(p.capitalize() for p in parts)


def _closest(word, terms, threshold):
    """The known term closest to `word` (case-insensitively), or None if nothing clears `threshold`."""
    lowered = word.lower()
    best, best_score = None, 0.0
    for term in terms:
        score = difflib.SequenceMatcher(None, lowered, term.lower()).ratio()
        if score > best_score:
            best, best_score = term, score
    return best if best_score >= threshold else None


def scan_terms(roots, *, min_length=4, stopwords=DEFAULT_STOPWORDS):
    """Distinctive project names found as the immediate subdirectories of each root, each normalized
    to its spoken form. This is the pluggable term source: point it at his workspace and it learns
    "Notecraft", "WaveShaper" and the rest off disk. Anything too short, hidden (leading "." or "_"), or
    in `stopwords` is dropped. A missing or unreadable root is skipped, not fatal."""
    terms = set()
    for root in roots:
        try:
            entries = sorted(Path(root).iterdir())
        except OSError:
            continue  # root doesn't exist / isn't readable - just contributes nothing
        for entry in entries:
            if entry.name.startswith((".", "_")) or not entry.is_dir():
                continue
            term = _normalize(entry.name)
            if len(term) >= min_length and term.lower() not in stopwords:
                terms.add(term)
    return terms


def correct_terms(text, terms, *, threshold=0.82):
    """Replace each near-miss token in `text` with the closest known term above `threshold`.

    Matching is on the bare word - punctuation is peeled off first so a trailing period can't drag
    the similarity down, then re-attached so "hideas." comes back "Notecraft." not "Notecraft".

    The 0.82 default was set from his real recordings: below ~0.78 ordinary words start getting
    corrupted - "are" -> "Harem", and (worst of all) the terminator "over" -> "Evolver", which would
    stop his turns from ever ending. 0.82 sits clear of that cliff while still catching real
    near-misses (his "hideas"/"notecraft" scores 0.86)."""
    if not text or not terms:
        return text
    out = []
    for token in text.split():
        prefix, word, suffix = _SPLIT.match(token).groups()
        match = _closest(word, terms, threshold) if word else None
        out.append(f"{prefix}{match}{suffix}" if match is not None else token)
    return " ".join(out)
