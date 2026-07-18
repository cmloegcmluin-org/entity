"""A timestamped, durable record of one session, written as it happens.

The terminal is where the conversation actually appears, and it scrolls away - so when something
goes wrong the only record of what the user saw was the user copying it out of his terminal by hand.
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
