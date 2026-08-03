"""The cleanup pass between his voice and the brain: spurious sentence breaks joined, instantly.

His natural pauses get transcribed as sentence breaks - "periods and capitalization on next word
even though they aren't natural points to end sentences." Three rounds of repairing that with a
small model failed in his hands: the model answered in four to eighty seconds when it answered at
all, sometimes echoed the text back unchanged, and the submit gained a flat eight seconds of
waiting for nothing ("consistently adds 8 seconds... and seems to have zero effect"). So the
model is retired from this path entirely.

What replaces it is deterministic, instant, and word-safe by construction. Two rules, both about
punctuation only - no word is ever added, dropped or respelled here:

1. A sentence mark followed by a LOWERCASE continuation is a break no writer makes on purpose -
   "what we need to do. in order for you to" - so the mark goes and the sentence heals.

2. A sentence mark followed by a word that cannot begin a sentence he meant to begin - "with a
   Claude agent", "at least your best attempt", "although I'm kind of surprised", "because that
   feature is already done" - is the same chop wearing a capital. Those openers are a closed list
   of conjunctions, subordinators and the two prepositions his transcript actually chops on;
   joining one costs at worst a comma where a period belonged, while leaving it costs the brain
   a fragment.

A capital that is NOT one of those openers is left alone: "...on anything other than yourself.
You're supposed to..." reads identically to a real boundary, and telling them apart needs meaning.
Guessing there would run his sentences together all day. Mishearings of his own terms ("one A
feature", "Entity Link copyfixes") are a different failure and belong to the transcriber's
vocabulary pass (see excephalon.vocabulary), not to this one.
"""

import re

# A sentence-ending mark followed by a lowercase letter: the transcriber closed a sentence that
# the speaker had not finished. The mark goes, the space stays, the words are untouched.
_SPURIOUS_BREAK = re.compile(r"(?<=\w)[.!?]+(\s+)(?=[a-z])")

# Openers that carry a clause on from the one before it. Split by what reads right in front of
# them: the first group takes a comma ("...in the app, at least your best attempt"), the second
# joins bare ("...for that one because that feature is already done").
_JOIN_WITH_COMMA = ("although", "though", "but", "and", "so", "or", "at least", "which",
                    "whereas", "instead", "rather")
_JOIN_BARE = ("because", "with", "without", "unless", "until", "while", "since", "than",
              "whether", "as long as", "so that")


def _openers(words, joiner):
    """One alternation matching any of these openers at a sentence's start, longest first, so
    "at least" wins over a bare "at" that is not in the list at all."""
    parts = sorted(words, key=len, reverse=True)
    return re.compile(r"(?<=\w)[.!?]+\s+(" + "|".join(re.escape(w) for w in parts)
                      + r")(?=[\s,;:.!?])", re.IGNORECASE), joiner


_CHOPPED_CLAUSE = (_openers(_JOIN_WITH_COMMA, ", "), _openers(_JOIN_BARE, " "))


def mend(text):
    """The text with its spurious sentence breaks healed - never anything else changed."""
    text = _SPURIOUS_BREAK.sub(r"\1", text)
    for pattern, joiner in _CHOPPED_CLAUSE:
        text = pattern.sub(lambda hit, joiner=joiner: joiner + hit.group(1).lower(), text)
    return text
