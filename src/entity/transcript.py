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


SESSION_MARK = "===== session ====="  # emitted between files; no log ever contains one


def past_lines(directory, *, current):
    """Every session ever recorded, oldest first - the whole thread above the live conversation,
    so scrolling back reaches the start rather than a cut with nothing on screen to explain it.
    `current` is this session's own file, which is live and excluded.

    A file writes its date at the top, so several sessions in one day used to read as the same
    date printed over and over. The two facts are separated here: the date is emitted only when
    it changes, and a session mark goes between files - one says which day, the other says a new
    conversation began.

    Unbounded on purpose: the whole archive is a few hundred kilobytes of text, and the window
    holds it rather than building it - see `bubbles.hold_back`.
    The filenames sort chronologically, so sorting them is sorting the history."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    lines, dated = [], None
    for path in sorted(path for path in directory.glob("*.log")
                       if current is None or path != Path(current)):
        try:
            session = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        if not session:
            continue  # nothing was said, so there is no session to divide off
        if lines:
            lines.append(SESSION_MARK)
        for line in session:
            day = day_of(line)
            if day is not None:
                if day == dated:
                    continue  # that date already stands above; repeating it reads as a glitch
                dated = day
            lines.append(line)
    return lines


def day_of(line):
    """The date on a file's day header, or None if the line is not one."""
    if line.startswith("===== ") and line.endswith(" =====") and line != SESSION_MARK:
        return line.strip("= ")
    return None


# The prefixes an agent's exchange is written under. Named here because the desk writes them and
# `parse_line` reads them back, and two spellings of one format is a bug nothing would catch.
ENTITY_SAID = "ENTITY> "
AGENT_SAID = "AGENT> "
AGENT_DID = "WORK> "  # what it ran, and what came back - the machinery under its words


# Both archives this reads: their own conversation (Console's prefixes) and an agent exchange (the
# desk's). "you" is whoever opened the exchange - them in their own thread, the Entity in an agent's.
_ROLE_PREFIXES = (
    ("you said: ", "you"),
    ("entity (heads-up)> ", "heads-up"),
    ("entity> ", "entity"),
    (ENTITY_SAID, "you"),
    (AGENT_SAID, "entity"),
    (AGENT_DID, "work"),
)


# The two kinds of break, deliberately unalike: a dated rule for the day, and a quiet caesura for
# one conversation ending and the next beginning. Made to look the same they read as one repeated
# thing, which is the confusion this pair exists to end.
DAY_BREAK = "───────  {}  ───────"
SESSION_BREAK = "•   •   •"  # filled, not middle dots: at this size those are three faint specks


def recent_turns(directory, keep=16):
    """The tail of the newest session, as (their words, the reply) pairs - the seed that lets a
    restarted process pick the conversation back up instead of greeting them as a stranger.

    "There should be a way to reload Entity so that it gets any fixes but without breaking the
    current session." The half of a restart that breaks the session is the lost thread; the
    transcript already holds it. Only the newest file: continuity is with the conversation they
    just had, and the older history is already in learned.md. A question with no reply under it
    (the line the session died on) is skipped, never stitched to the next answer - a seed where
    answers sit under the wrong questions is worse than no seed at all.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return []
    newest = max(directory.glob("*.log"), default=None)  # filenames sort chronologically
    if newest is None:
        return []
    try:
        lines = newest.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    turns, question = [], None
    for line in lines:
        parsed = parse_line(line)
        if parsed is None:
            continue
        role, _, text = parsed
        if role == "you":
            question = text  # a question already waiting is the one the session died on - dropped
        elif role == "entity" and question is not None:
            turns.append((question, text))
            question = None
    return turns[-keep:]


def parse_line(line):
    """Read one recorded line back as (role, time, text), or None if it isn't conversation.

    The prefixes are the ones Console writes; reading its own archive back is what lets past
    sessions appear in the window as the conversation they were, not as log lines.
    """
    line = line.rstrip()
    if line == SESSION_MARK:
        # Its own role, not "status": the window offers to copy a whole session from this line,
        # and recognising it by its display text would be reading the label to find the thing.
        return "session", "", SESSION_BREAK
    day = day_of(line)
    if day is not None:
        # The date each file writes once a day. Scrolling back through every session ever is a
        # wall without it, so it comes back as the break it marks rather than as nothing. Its own
        # role, so the date is read off the entry rather than back out of the line it draws.
        return "day", day, DAY_BREAK.format(day)
    if not line.startswith("[") or "] " not in line:
        return None
    stamp, _, body = line[1:].partition("] ")
    body = body.strip()
    if not body:
        return None
    for prefix, role in _ROLE_PREFIXES:
        if body.startswith(prefix):
            return role, stamp, body[len(prefix):]
        if body == prefix.rstrip():
            # The marker with nothing after it - a blank line in what was said. A written line
            # keeps no trailing space by the time it is read back, so without this the marker
            # itself was drawn, centred, in the middle of the tab: "AGENT>", "WORK>".
            return None
    return "status", stamp, body
