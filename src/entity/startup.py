"""Read the user's standing startup instructions from a file, so he doesn't retype them each launch.

He kept pasting the same long kickoff (resume this session, tail the log, ...) every time the
Entity started. Drop that text in the startup-instructions file instead and it becomes his first
turn automatically - spoken to and answered exactly as if he'd just said it.
"""

from pathlib import Path


def load_startup_instructions(path):
    """The stripped contents of the startup-instructions file, or None if it's missing or blank."""
    path = Path(path)
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


class ScriptedFirstTurn:
    """Wrap an STT so its first `listen()` returns a pre-supplied line - the user's startup
    instructions - then defers to the real STT for every turn after. With nothing to play it's
    transparent, so a missing file changes nothing."""

    def __init__(self, inner, first):
        self._inner = inner
        self._first = first  # the line to play once, or None to stay out of the way

    def listen(self):
        if self._first is not None:
            first, self._first = self._first, None
            return first
        return self._inner.listen()
