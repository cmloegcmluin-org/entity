"""A timestamped, readable transcript of a fleet-driving session.

the user can't watch the agents' screens, so besides hearing them by voice he wants a durable
record he can scroll back through - and he wants it in the clean, labelled ENTITY/AGENT format
with a timestamp on every line, written from the start of the session (not reconstructed after
the fact). `FleetLog` is that writer: each entry is stamped with the current time and prefixed
with who said it. The clock is injected so tests are deterministic; writes are locked because the
agents' worker threads and the drive loop log concurrently. `NullFleetLog` is the do-nothing
stand-in for runs (and tests) that don't want a file.
"""

import threading
from datetime import datetime
from pathlib import Path


class FleetLog:
    def __init__(self, path, *, clock=datetime.now, timefmt="%H:%M:%S"):
        self._path = Path(path)
        self._clock = clock
        self._timefmt = timefmt
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def entity(self, text):
        """Something the Entity said or did, on the user's behalf."""
        self._write("ENTITY", text)

    def agent(self, name, text):
        """Something the named agent reported back."""
        self._write(f"AGENT {name}", text)

    def _write(self, speaker, text):
        stamp = self._clock().strftime(self._timefmt)
        lines = str(text).splitlines() or [""]
        block = "".join(f"[{stamp}] {speaker}: {line}\n" for line in lines)
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as handle:
                handle.write(block)


class NullFleetLog:
    """Swallows every entry - used when a session shouldn't leave a file behind."""

    def entity(self, text):
        pass

    def agent(self, name, text):
        pass
