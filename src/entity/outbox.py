"""A little queue for things the Entity wants to say on its own - chiefly word from an agent it's
driving. A background producer pushes; the conversation loop drains and speaks the messages the
next time it's the Entity's turn to talk. `arrived` lets a quiet moment (a lull while it's waiting
for the user to start speaking) be interrupted so the message goes out promptly, rather than sitting
until he happens to say something.
"""

import threading
from collections import deque


class Outbox:
    def __init__(self):
        self._items = deque()
        self._lock = threading.Lock()
        self.arrived = threading.Event()  # set while something is waiting to be spoken

    def push(self, message):
        with self._lock:
            self._items.append(message)
        self.arrived.set()

    def drain(self):
        """Take everything queued (in arrival order) and clear the waiting signal."""
        with self._lock:
            items = list(self._items)
            self._items.clear()
            self.arrived.clear()
        return items

    def __bool__(self):
        with self._lock:
            return bool(self._items)
