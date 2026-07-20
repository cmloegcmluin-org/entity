"""What a message names that can be opened: a web address, or a path on this machine - what counts
as one, what opens it, and how it is said aloud.

They are not technical outside code, so the useful behaviour is not that a path is coloured - it is
that it OPENS. Entity writes real Windows paths into the conversation constantly, and reading one
off the screen to type somewhere else is exactly the friction the window exists to remove.

Spoken is the other half of the same question, and the same answer serves both: a thing worth
turning into a link is a thing nobody reads out. What is written stays whole on the page, and
`as_spoken` is what the voice gets instead.

All of it is decided here, purely, so it is settled without a display; the page only paints what
this finds.
"""

import os
import re
import webbrowser
from pathlib import Path, PureWindowsPath

# A drive-letter path, a UNC share, or an http(s) address. Nothing looser: a bare `src/entity` is
# indistinguishable from "and/or" or "they/she", and a wrong thing offered as openable is worse than
# a right one left plain.
_LINK = re.compile(r"https?://\S+|[A-Za-z]:[\\/]\S+|\\\\[^\s\\]+\\\S+")

# Entity writes these inside sentences, so the full stop after a filename is the sentence's and
# the bracket around an address is the sentence's too.
_LEADING, _TRAILING = "\"'<([{", ".,;:!?\"'>)]}"


def link_in(word):
    """What this one word opens, or None."""
    target = word.strip().lstrip(_LEADING).rstrip(_TRAILING)
    return target if _LINK.fullmatch(target) else None


MAX_PATH_WORDS = 8  # a path with more spaces than this is not worth probing the disk over


def link_parts(text, *, exists=os.path.exists):
    """`text` split into what can be opened and what cannot, as [{"text", "link"}].

    The hard case is a space: "C:\\Users\\ada\\Field Notes\\inbox" cannot be told from a
    path followed by another word by looking at the text alone - which is why a single broken link
    is what they saw. So the filesystem is asked. A drive-letter or UNC match is extended across the
    following words to the longest run that actually exists on disk; a run that exists nowhere
    stays the one word it was, exactly as before, and a URL (which can hold no space) is always the
    one word. The page draws only what this returns, so the rule lives here, where it is tested."""
    words = text.split(" ")
    parts, plain, index = [], [], 0
    while index < len(words):
        if link_in(words[index]) is None:
            plain.append(words[index])
            index += 1
            continue
        if plain:
            parts.append({"text": " ".join(plain) + " ", "link": ""})
            plain = []
        span, target = _widest(words, index, exists)
        raw = " ".join(words[index:index + span])
        lead = raw[:len(raw) - len(raw.lstrip(_LEADING))]  # the sentence's own "(" stays outside
        trail = raw[len(lead) + len(target):]              # and its own trailing "." does too
        for piece in ({"text": lead, "link": ""}, {"text": target, "link": target},
                      {"text": trail + " ", "link": ""}):
            if piece["text"]:
                parts.append(piece)
        index += span
    if plain:
        parts.append({"text": " ".join(plain), "link": ""})
    return parts


def offers(target, *, exists=os.path.exists):
    """Would this module, shown exactly this string, turn the whole of it into one link?

    What `/open` asks before opening anything: a POST that opens whatever it is handed is a way to
    run things by talking to the port, so it opens only what the page was actually offered - and
    "offered" is defined by the very function that offered it, spaces and all."""
    return [part["link"] for part in link_parts(target, exists=exists) if part["link"]] == [target]


def _widest(words, index, exists):
    """How many words the path at `index` really spans, and the path itself. The one-word match is
    the floor - offered whether or not it exists, since Entity names files a moment before making
    them. A wider run only wins when the disk confirms it, so a real word after a real path is
    never swallowed into it."""
    base = link_in(words[index])
    if base.startswith(("http://", "https://")):
        return 1, base
    widest_span, widest = 1, base
    for span in range(2, min(MAX_PATH_WORDS, len(words) - index) + 1):
        # `base` already proved this is a drive/UNC path; an extension across a space cannot match
        # `_LINK` (it forbids whitespace), so existence on disk is the whole test for one.
        candidate = " ".join(words[index:index + span]).lstrip(_LEADING).rstrip(_TRAILING)
        if exists(candidate):
            widest_span, widest = span, candidate
    return widest_span, widest


# What a person says instead of reading an address out. A stand-in rather than nothing: dropping
# it would leave a sentence that no longer says there IS anything to open, and they have already
# objected to hearing less than what was written.
SPOKEN_ADDRESS = "the link"


def as_spoken(text):
    """`text` as it should be SAID - the written form stays on screen untouched.

    Nobody reads an address out character by character, and a Windows path read aloud is a minute
    of "backslash". What is on screen is still the real thing, so it can be read and clicked."""
    return " ".join(_said_aloud(word) for word in text.split())


def _said_aloud(word):
    """One word, with the sentence's own punctuation left around whatever stands in for it."""
    core = word.lstrip(_LEADING)
    lead = word[:len(word) - len(core)]
    kept = len(core.rstrip(_TRAILING))
    core, trail = core[:kept], core[kept:]
    if link_in(core) is None:
        return word
    return lead + _stand_in(core) + trail


def _stand_in(target):
    """An address is "the link"; a path is its last part, which is the part a person would say -
    "it's in profile.md", never the eight folders above it."""
    if target.startswith(("http://", "https://")):
        return SPOKEN_ADDRESS
    return PureWindowsPath(target).name or SPOKEN_ADDRESS


def _on_this_machine(where):
    """Windows' own "open this": a folder in Explorer, a file in whatever owns that kind."""
    os.startfile(where)


def open_link(target, *, browser=webbrowser.open, shell=_on_this_machine):
    """Open what was clicked - an address in the browser, anything else on this machine.

    A path Entity has named but not written yet opens the nearest folder above it that IS there.
    It names a file in the same breath as making it, so a click can land a moment early, and a
    click that opens nothing at all reads as the window being broken rather than as being early.
    If nothing in the path exists, nothing opens - there is no such place to show."""
    if target.startswith(("http://", "https://")):
        browser(target)
        return
    where = Path(target)
    while not where.exists() and where.parent != where:
        where = where.parent
    if where.exists():
        shell(str(where))
