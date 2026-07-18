"""Custom vocabulary: bias transcription toward the words the user actually uses.

That's two kinds of word, and a general-purpose model knows neither: names he coined ("Notecraft",
"WaveShaper", "Skylark") and the domain vocabulary of the fields he lives in - acoustic music and
Bayesian notation, film, his health, the people he works with. Parakeet renders both as whatever
ordinary words sound closest ("high ideas", "gina"). onnx-asr's Parakeet path exposes no hotword /
contextual-biasing hook (its RecognizeOptions cover only Whisper/Canary language flags), so the
bias happens AFTER transcription: `correct_terms` swaps any near-miss - a single word or a whole
phrase - for the closest known term. The term list is pluggable: `scan_terms` reads project names
off his filesystem and his lexicon supplies the rest, but tests inject a plain list, so none of
this needs a real disk.
"""

import difflib
import re
from pathlib import Path

_SPLIT = re.compile(r"^(\W*)(.*?)(\W*)$")  # leading punctuation, the bare word, trailing punctuation
_SEPARATORS = re.compile(r"[ _\-]+")
_SENTENCE_END = (".", "!", "?")

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


def _letters(text):
    """Just the letters and digits, lowercased - the form a term is compared in when the spaces
    can't be trusted, so "Git Bash" and a run-together "GitMash" still line up."""
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _closest(word, candidates, threshold):
    """The term closest to `word`, or None if nothing clears `threshold`. `candidates` are
    (what to compare against, the term to hand back) pairs, so the caller decides whether the
    comparison keeps the spaces or closes them up."""
    best, best_score = None, 0.0
    for compared, term in candidates:
        # Strings this different in length can't clear the bar however well they line up
        # (ratio <= 2*min/total), so skip the comparison rather than pay for it.
        if 2 * min(len(word), len(compared)) < threshold * (len(word) + len(compared)):
            continue
        score = difflib.SequenceMatcher(None, word, compared).ratio()
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


def _match_at(tokens, start, run_together, by_length, longest, threshold):
    """The (window size, term) of the LONGEST run of words at `start` that matches a known term, or
    None. Longest-first so "Bayesian notation" wins over a stray one-word match inside it.

    A run of words is only compared against terms of the same word count - two ordinary words are
    never glued into a coined name. A SINGLE token is the exception: it's compared against every
    term with the spaces closed up, because speech-to-text routinely runs a two-word name together
    ("Git Bash" comes back as the one word "GitMash") - and with only one token in play, there's no
    neighbouring word for the term to wrongly swallow."""
    for size in range(min(longest, len(tokens) - start), 0, -1):
        window = tokens[start:start + size]
        if size > 1 and any(token[2].endswith(_SENTENCE_END) for token in window[:-1]):
            continue  # never glue a phrase together across a sentence boundary
        words = " ".join(token[1] for token in window if token[1])
        if not words:
            continue
        if size == 1:
            match = _closest(_letters(words), run_together, threshold)
        else:
            match = _closest(words.lower(), by_length.get(size, ()), threshold)
        if match is not None:
            return size, match
    return None


def correct_terms(text, terms, *, threshold=0.82):
    """Rewrite `text`, replacing each near-miss with the closest known term above `threshold`.

    A term can be one word or several - domain vocabulary usually is ("Bayesian notation"), so runs
    of words are matched as phrases, longest first, not one token at a time. Punctuation is peeled
    off before comparing so a trailing period can't drag the similarity down, then re-attached, and
    a phrase is never glued together across a sentence boundary.

    The 0.82 default was set from his real recordings: below ~0.78 ordinary words start getting
    corrupted - "are" -> "Harem", and (worst of all) the terminator "over" -> "Evolver", which would
    stop his turns from ever ending. 0.82 sits clear of that cliff while still catching real
    near-misses (his "hideas"/"notecraft" scores 0.86)."""
    if not text or not terms:
        return text
    # Both comparison forms, built once: phrases matched word-for-word, and every term with its
    # spaces closed up for the case where speech-to-text ran the whole name into one token.
    by_length = {}
    for term in terms:
        by_length.setdefault(len(term.split()), []).append((term.lower(), term))
    run_together = [(_letters(term), term) for term in terms]
    tokens = [_SPLIT.match(token).groups() for token in text.split()]
    out = []
    index = 0
    while index < len(tokens):
        found = _match_at(tokens, index, run_together, by_length, max(by_length), threshold)
        if found is None:
            prefix, word, suffix = tokens[index]
            out.append(f"{prefix}{word}{suffix}")
            index += 1
            continue
        size, term = found
        out.append(f"{tokens[index][0]}{term}{tokens[index + size - 1][2]}")
        index += size
    return " ".join(out)
