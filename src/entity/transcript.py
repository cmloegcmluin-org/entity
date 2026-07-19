"""A timestamped, durable record of one session, written as it happens.

The terminal is where the conversation actually appears, and it scrolls away - so when something
goes wrong the only record of what the user saw was whatever they copied out of the terminal by hand.
This writes the same lines to a file as they're printed, stamped with the time, so a session can be
read back afterwards. The clock is injected so tests are deterministic; writes are locked because
background workers and the conversation loop both log.
"""

import threading
from datetime import datetime
from pathlib import Path


class Transcript:
    def __init__(self, path, *, clock=datetime.now, timefmt="%H:%M:%S"):
        self.path = Path(path)
        self._clock = clock
        self._timefmt = timefmt
        self._lock = threading.Lock()
        self._last_day = None  # the date the last line was written under, to mark day rollovers
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, text, *, prefix=""):
        # The date lives in the filename and in a header written once per day; the lines themselves
        # carry only the time, and a fresh header marks a session that runs past midnight.
        now = self._clock()
        stamp = now.strftime(self._timefmt)
        lines = str(text).splitlines() or [""]
        body = "".join(f"[{stamp}] {prefix}{line}\n" for line in lines)
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as handle:
                if now.date() != self._last_day:
                    handle.write(f"===== {now.strftime('%Y-%m-%d')} =====\n")
                    self._last_day = now.date()
                handle.write(body)


def past_lines(directory, *, current):
    """Every session ever recorded, oldest first - the whole thread above the live conversation,
    so scrolling back reaches the start rather than a cut with nothing on screen to explain it.
    `current` is this session's own file, which is live and excluded.

    Unbounded on purpose: the whole archive is a few hundred kilobytes of text, and the window
    holds it rather than building it - see `bubbles.hold_back`.
    The filenames sort chronologically, so sorting them is sorting the history."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    lines = []
    for path in sorted(path for path in directory.glob("*.log")
                       if current is None or path != Path(current)):
        try:
            lines.extend(path.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            continue
    return lines


# Both archives this reads: their own conversation (Console's prefixes) and an agent exchange (the
# desk's). "you" is whoever opened the exchange - them in their own thread, the Entity in an agent's.
_ROLE_PREFIXES = (
    ("you said: ", "you"),
    ("entity (heads-up)> ", "heads-up"),
    ("entity> ", "entity"),
    ("ENTITY> ", "you"),
    ("AGENT> ", "entity"),
)


DAY_BREAK = "───────  {}  ───────"


def parse_line(line):
    """Read one recorded line back as (role, time, text), or None if it isn't conversation.

    The prefixes are the ones Console writes; reading its own archive back is what lets past
    sessions appear in the window as the conversation they were, not as log lines.
    """
    line = line.rstrip()
    if line.startswith("===== ") and line.endswith(" ====="):
        # The date each file writes once a day. Scrolling back through every session ever is a
        # wall without them, so it comes back as the break it marks rather than as nothing.
        day = line.strip("= ")
        return "status", day, DAY_BREAK.format(day)
    if not line.startswith("[") or "] " not in line:
        return None
    stamp, _, body = line[1:].partition("] ")
    body = body.strip()
    if not body:
        return None
    for prefix, role in _ROLE_PREFIXES:
        if body.startswith(prefix):
            return role, stamp, body[len(prefix):]
    return "status", stamp, body
