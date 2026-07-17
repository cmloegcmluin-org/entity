"""Watch an inbox directory for word from the agents the Entity is driving, and hand it to the
outbox so the Entity can pass it on to the user.

This is how an agent reaches the user without him watching its screen: the Entity tells each agent to
write any question or "ready for review" note as a line to `runtime/agent-inbox/<name>.txt`. A
background thread tails those files; each complete new line becomes an outbox message, spoken at the
next lull. Deliberately dumb - plain file polling, byte-offset per file, no OS-specific watching -
so it just works on Windows and can't corrupt the brain's own message stream.
"""

import threading
import time
from pathlib import Path


class InboxWatcher:
    def __init__(self, directory, outbox, *, poll_interval=1.0, sleep=time.sleep):
        self._dir = Path(directory)
        self._outbox = outbox
        self._poll_interval = poll_interval
        self._sleep = sleep
        self._offsets = {}  # file -> bytes already surfaced
        self._stop = threading.Event()
        # Seed offsets past whatever's already there, so a fresh start doesn't replay old questions.
        for path in self._files():
            self._offsets[path] = self._size(path)

    def _files(self):
        return sorted(self._dir.glob("*.txt")) if self._dir.exists() else []

    @staticmethod
    def _size(path):
        try:
            return path.stat().st_size
        except OSError:
            return 0

    def poll_once(self):
        for path in self._files():
            size = self._size(path)
            start = self._offsets.get(path, 0)
            if size < start:  # file was truncated or rewritten smaller - resync from the top
                start = 0
            if size <= start:
                self._offsets[path] = size
                continue
            try:
                with open(path, "rb") as handle:
                    handle.seek(start)
                    chunk = handle.read(size - start)
            except OSError:
                continue
            newline = chunk.rfind(b"\n")
            if newline == -1:
                continue  # only a half-written line so far; wait for it to finish
            self._offsets[path] = start + newline + 1
            for line in chunk[: newline + 1].decode("utf-8", "replace").splitlines():
                line = line.strip()
                if line:
                    self._outbox.push(line)

    def run(self):
        while not self._stop.is_set():
            self.poll_once()
            self._sleep(self._poll_interval)

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def stop(self):
        self._stop.set()
