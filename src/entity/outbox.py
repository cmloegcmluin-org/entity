"""A little queue for things the Entity wants to say on its own - chiefly word from an agent it's
driving. A background producer pushes; the conversation loop drains and speaks the messages the
next time it's the Entity's turn to talk. `arrived` lets a quiet moment (a lull while it's waiting
for the user to start speaking) be interrupted so the message goes out promptly, rather than sitting
until they happen to say something.
"""

import threading
from collections import deque


class News(str):
    """One thing waiting to be said, and which agent it is about.

    A string, because everything downstream speaks it, joins it and matches on it. But the agent's
    name has to survive the queue too: when several agents are ready at once they are read out
    numbered so one can be picked, and working the name back out of the message text would be
    reading the label to find the thing - two of the four kinds of news the Entity queues do not
    carry it in any fixed place at all.

    `composed` says the BRAIN wrote these words (the narrator asked it to): spoken as its own,
    they need no unwritten-lines ledger entry - it remembers saying them the way it remembers any
    reply. App-authored news stays composed=False and is read back to it next turn."""

    about = None  # the agent, when there is one
    composed = False  # whether the brain itself wrote the words

    def __new__(cls, message, about=None, composed=False):
        news = super().__new__(cls, message)
        news.about = about
        news.composed = composed
        return news


class Outbox:
    def __init__(self):
        self._items = deque()
        self._lock = threading.Lock()
        self.arrived = threading.Event()  # set while something is waiting to be spoken

    def push(self, message, about=None, composed=False):
        with self._lock:
            self._items.append(News(message, about, composed))
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
