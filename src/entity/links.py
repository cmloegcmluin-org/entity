"""What a message names that can be opened: a web address, or a path on this machine.

He is not technical outside code, so the useful behaviour is not that a path is coloured - it is
that it OPENS. Entity writes real Windows paths into the conversation constantly, and reading one
off the screen to type somewhere else is exactly the friction the window exists to remove.

What counts as one is decided here, purely, so it is settled without a display; `bubbles.py` only
paints what this finds.
"""

import os
import re
import webbrowser
from pathlib import Path

# A drive-letter path, a UNC share, or an http(s) address. Nothing looser: a bare `src/entity` is
# indistinguishable from "and/or" or "he/she", and a wrong thing offered as openable is worse than
# a right one left plain.
_LINK = re.compile(r"https?://\S+|[A-Za-z]:[\\/]\S+|\\\\[^\s\\]+\\\S+")

# Entity writes these inside sentences, so the full stop after a filename is the sentence's and
# the bracket around an address is the sentence's too.
_LEADING, _TRAILING = "\"'<([{", ".,;:!?\"'>)]}"


def link_in(word):
    """What this word opens, or None.

    A path with a space in it cannot be told from a path followed by another word, so those are
    not offered rather than offered wrong."""
    target = word.strip().lstrip(_LEADING).rstrip(_TRAILING)
    return target if _LINK.fullmatch(target) else None


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
