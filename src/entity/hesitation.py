"""Leave out the sounds a speaker made while they were thinking.

People say "um" and "uh" while they find the next word, and the model - a speech recognizer, doing
its job - writes both down. Nothing about them is worth keeping: a turn is read back for what it
says, and a real transcript is thick with them.

Only these two sounds: the wordless ones ("mm", "hmm") are already dropped as backchannel, and the
filler made of real words ("like", "you know") can't be cut without cutting the sentences that use
those words for their meaning.

Dropping a word rather than relabelling one has one consequence worth handling: a sound that opened
a sentence was carrying that sentence's capital, so the word left standing takes it.
"""

import re

# "um" and "uh", however long the sound was held - the model spells a held sound out at the length
# it was held. No English word is spelled from a u followed only by m and h.
_HESITATION = re.compile(r"u[mh]+", re.IGNORECASE)
_SENTENCE_END = (".", "!", "?")


def without_hesitations(text):
    """`text` with every hesitation dropped, and the capital it was carrying handed on."""
    kept, opening = [], False
    tokens = text.split()
    for index, token in enumerate(tokens):
        if _is_hesitation(token):
            opening = opening or _opens_a_sentence(tokens, index)
            continue
        kept.append(_capitalized(token) if opening else token)
        opening = False
    return " ".join(kept)


def _opens_a_sentence(tokens, index):
    """Whether the token at `index` is the first word of a sentence. A token with no letters is
    read straight past: it's punctuation the model set down, not a word they said."""
    while index and not any(character.isalpha() for character in tokens[index - 1]):
        index -= 1
    return index == 0 or tokens[index - 1].endswith(_SENTENCE_END)


def _is_hesitation(token):
    """Whether `token` is one of the sounds, however the model punctuated it - a comma around the
    sound was the pause being written down, and it has nothing left to sit beside once the sound is
    gone. A whole word of it and nothing less: an *umbrella* they actually said is a worse thing to
    lose than an "um" is to keep."""
    return _HESITATION.fullmatch("".join(c for c in token if c.isalpha())) is not None


def _capitalized(token):
    """`token` with its first letter made a capital and the rest left alone - never
    `str.capitalize`, which would take the rest of an "OK" or a "PDF" back down."""
    return token[:1].upper() + token[1:]
