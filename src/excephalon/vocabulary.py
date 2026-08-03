"""Custom vocabulary: bias transcription toward the words this particular user actually uses.

That's two kinds of word, and a general-purpose model knows neither: the names they coined
("Notecraft", "WaveShaper") and the domain vocabulary of the fields they work in - the terms of
art, the proper nouns, the people they collaborate with. Parakeet renders both as whatever
ordinary words sound closest ("note craft"). onnx-asr's Parakeet path exposes no hotword /
contextual-biasing hook (its RecognizeOptions cover only Whisper/Canary language flags), so the
bias happens AFTER transcription: `correct_terms` swaps any near-miss - a single word or a whole
phrase - for the closest known term. The term list is pluggable: `scan_terms` reads project names
off the filesystem and the user's lexicon supplies the rest, but tests inject a plain list, so
none of this needs a real disk.
"""

import difflib
import re
from pathlib import Path

_SPLIT = re.compile(r"^(\W*)(.*?)(\W*)$")  # leading punctuation, the bare word, trailing punctuation
_SEPARATORS = re.compile(r"[ _\-]+")
_SENTENCE_END = (".", "!", "?")

# Mishearings that similarity can never catch, because what comes back is ordinary English. Every
# one of these was counted in real session transcripts: "Claude" arrived as "cloud" seven times
# against seven correct ones, and "worktree" as "Work Tree". Kept to what was actually observed and
# to this app's own vocabulary - anything personal belongs in their own list, which the window shows
# these beside so the whole set is one thing to read.
DEFAULT_TRANSLATIONS = {
    "cloud agent": "Claude agent",
    "cloud agents": "Claude agents",
    "claud agent": "Claude agent",
    "claud agents": "Claude agents",
    "work tree": "worktree",
    "work trees": "worktrees",
}

# Generic folder names that are ordinary English (or infrastructure) and so aren't worth biasing
# toward - and, being common words, would invite false corrections of normal speech.
DEFAULT_STOPWORDS = frozenset({
    "shared", "vision", "pytorch", "python", "core", "common", "src", "lib", "libs",
    "test", "tests", "temp", "tmp", "data", "assets", "build", "dist", "node_modules",
    "venv", "env", "archive", "backup", "old", "new", "misc", "projects", "workspace",
    "documents", "downloads", "desktop", "scripts", "utils", "vendor",
})


def _normalize(name):
    """The spoken/written form of a directory name: "wave_shaper" -> "WaveShaper", "notecraft" ->
    "Notecraft". A name that already carries its own capitalization (OpenGLDemo) keeps it, minus
    separators."""
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
    to its spoken form. This is the pluggable term source: point it at a workspace and it learns
    "Notecraft", "WaveShaper" and the rest off disk. Anything too short, hidden (leading "." or
    "_"), or in `stopwords` is dropped. A missing or unreadable root is skipped, not fatal."""
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


def translations_in_force(their=None):
    """Every named translation that will actually be applied: the ones that ship, with their own
    written over them. One rule in one place, because the window shows this list as the list in
    force and a second merge somewhere else would eventually disagree with it."""
    return DEFAULT_TRANSLATIONS | dict(their or {})


def _lookups(terms, translations):
    """Every table a match needs, built once per call.

    `named` is the exact list - what they heard on the left, what they said on the right, looked up by
    word count. `nearly` is the fuzzy one, also by word count, so two ordinary words are never
    glued into a coined name. `run_together` is every term with its spaces closed up, for the case
    where speech-to-text ran a whole name into one token."""
    named, nearly = {}, {}
    for heard, said in dict(translations).items():
        named.setdefault(len(heard.split()), {})[heard.lower()] = said
    for term in terms:
        nearly.setdefault(len(term.split()), []).append((term.lower(), term))
    return {
        "named": named,
        "nearly": nearly,
        "run_together": [(_letters(term), term) for term in terms],
        "longest": max([*named, *nearly, 1]),
    }


def _match_at(tokens, start, lookups, threshold):
    """The (window size, term) of the LONGEST run of words at `start` that matches something known,
    or None. Longest-first so "Bayesian inference" wins over a stray one-word match inside it.

    A named translation is checked before the fuzzy one at each size, because it is the case where
    similarity cannot help: "cloud agent" for "Claude agent" is two ordinary English words, and no
    threshold that leaves normal speech alone will ever catch it.

    A SINGLE token is the fuzzy exception: it's compared against every term with the spaces closed
    up, because speech-to-text routinely runs a two-word name together ("Git Bash" comes back as
    the one word "GitMash") - and with only one token in play, there's no neighbouring word for the
    term to wrongly swallow."""
    for size in range(min(lookups["longest"], len(tokens) - start), 0, -1):
        window = tokens[start:start + size]
        if size > 1 and any(token[2].endswith(_SENTENCE_END) for token in window[:-1]):
            continue  # never glue a phrase together across a sentence boundary
        words = " ".join(token[1] for token in window if token[1])
        if not words:
            continue
        named = lookups["named"].get(size, {}).get(words.lower())
        if named is not None:
            return size, named
        if size == 1:
            match = _closest(_letters(words), lookups["run_together"], threshold)
        else:
            match = _closest(words.lower(), lookups["nearly"].get(size, ()), threshold)
        if match is not None:
            return size, match
    return None


def correct_terms(text, terms, *, translations=(), threshold=0.82):
    """Rewrite `text`, replacing each near-miss with the closest known term above `threshold`, and
    each phrase named in `translations` with what they actually said.

    A term can be one word or several - domain vocabulary usually is ("Bayesian inference"), so runs
    of words are matched as phrases, longest first, not one token at a time. Punctuation is peeled
    off before comparing so a trailing period can't drag the similarity down, then re-attached, and
    a phrase is never glued together across a sentence boundary.

    The 0.82 default was set from real recordings: below ~0.78 ordinary words start getting
    corrupted - "are" -> a project called "Arena" (0.75), and worst of all the turn terminator
    "over" -> "Overlay" (0.73), which would stop a turn from ever ending. 0.82 sits clear of that
    cliff while still catching real near-misses ("notcraft"/"Notecraft" scores 0.94). What that
    threshold can never reach is what `translations` is for."""
    if not text or (not terms and not translations):
        return text
    lookups = _lookups(terms, translations)
    tokens = [_SPLIT.match(token).groups() for token in text.split()]
    out = []
    index = 0
    while index < len(tokens):
        found = _match_at(tokens, index, lookups, threshold)
        if found is None:
            prefix, word, suffix = tokens[index]
            out.append(f"{prefix}{word}{suffix}")
            index += 1
            continue
        size, term = found
        out.append(f"{tokens[index][0]}{term}{tokens[index + size - 1][2]}")
        index += size
    return " ".join(out)
