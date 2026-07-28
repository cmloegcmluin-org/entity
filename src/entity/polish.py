"""The cleanup pass between his voice and the brain: spurious sentence breaks joined, instantly.

His natural pauses get transcribed as sentence breaks - "periods and capitalization on next word
even though they aren't natural points to end sentences." Three rounds of repairing that with a
small model failed in his hands: the model answered in four to eighty seconds when it answered at
all, sometimes echoed the text back unchanged, and the submit gained a flat eight seconds of
waiting for nothing ("consistently adds 8 seconds... and seems to have zero effect"). So the
model is retired from this path entirely.

What replaces it is deterministic, instant, and word-safe by construction: a period followed by a
LOWERCASE continuation is a break no writer makes on purpose - "what we need to do. in order for
you to" - so the period goes and the sentence heals. That is the whole rule. Chop that lands
before a capitalized word is left alone: telling "That Instead of creating" from a real sentence
boundary needs semantics, and the price of guessing is eating his meaning. Mishearings of his own
terms are the transcriber's fuzzy vocabulary pass's job (see entity.vocabulary), not this one's.
"""

import re

# A sentence-ending mark followed by a lowercase letter: the transcriber closed a sentence that
# the speaker had not finished. The mark goes, the space stays, the words are untouched.
_SPURIOUS_BREAK = re.compile(r"(?<=\w)[.!?]+(\s+)(?=[a-z])")


def mend(text):
    """The text with its spurious sentence breaks healed - never anything else changed."""
    return _SPURIOUS_BREAK.sub(r"\1", text)
