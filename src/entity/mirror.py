"""The conversation as a window shows it, with no window in it.

The Console reports each line and WHO said it, the Dictation reports draft text, mic state and
levels, and everything crosses into the window through one thread-safe feed. What can be wrong -
the message model, the feed, where each session starts - lives here and is tested without a
display; `web.py` serves it and the page draws it.

The window's own taskbar identity lives here too, because a shortcut has to be stamped with the
same id and there must be one definition of it rather than a copy in the installer.
"""

import queue
import time

from entity.transcript import parse_line

# Windows groups taskbar buttons by AppUserModelID, and a process that declares none inherits the
# identity of whatever other python-hosted app already owns a button - the Entity window turned up
# under an unrelated app's icon, wearing that app's icon. Declaring one gives it its own.
APP_ID = "Entity.VoiceCompanion"

# Who sits on which side of the thread. A heads-up is Entity talking out of turn, so it takes
# Entity's side and says so in the name.
SIDES = {"you": "right", "entity": "left", "heads-up": "left"}


def _clock():
    return time.strftime("%H:%M:%S")


class TranscriptModel:
    """The conversation the window shows: entries of {role, stamp, text, historical}.

    Pure - ops in, entries out - so every rendering decision can be tested without a display. An
    "overwrite" op is the carriage-return trick the terminal uses for the ignore counter: it opens
    a live status entry, and later ones replace it in place.
    """

    def __init__(self, clock=_clock):
        self.entries = []
        self._clock = clock
        self._counter = None  # index of the live ignore-counter entry, if one is open

    def apply(self, op, payload):
        if op == "message":
            role, text = payload
            self._add(role, text)
        elif op == "history":
            parsed = parse_line(payload)
            if parsed is not None:
                role, stamp, text = parsed
                self._add(role, text, stamp=stamp, historical=True)
        elif op == "line":
            if str(payload).strip():
                self._add("status", str(payload))
        elif op == "overwrite":
            self._overwrite(payload)

    def _add(self, role, text, *, stamp=None, historical=False):
        self._counter = None
        self.entries.append({
            "role": role,
            "stamp": stamp or self._clock(),
            "text": text.strip(),
            "historical": historical,
        })

    def _overwrite(self, text):
        if text == "\n":  # the run is being closed; its final count stays as an ordinary entry
            self._counter = None
            return
        body = text.lstrip("\r")
        if self._counter is not None:
            self.entries[self._counter]["text"] = body
            return
        self._add("status", body)
        self._counter = len(self.entries) - 1


class TranscriptFeed:
    """Thread-safe hand-off from the conversation loop and the dictation pump to the window."""

    def __init__(self):
        self._ops = queue.SimpleQueue()

    def push(self, op, payload):
        self._ops.put((op, payload))

    def drain(self):
        ops = []
        while True:
            try:
                ops.append(self._ops.get_nowait())
            except queue.Empty:
                return ops


class Mirror:
    """What the pages show: the conversation, the mic's state, and what dictation has typed.

    The Tk window drained the feed on a timer of its own. Here the page's poll is the timer, so
    nothing is pumped while nothing is looking - and the ops arrive in the order they were sent,
    because one place drains them."""

    def __init__(self, feed, *, clock=None):
        self.model = TranscriptModel(clock=clock) if clock else TranscriptModel()
        self._feed = feed
        # Not "muted": at birth the mic doesn't exist YET - the models it needs are still loading.
        # The pump's first report (pushed the moment it starts) is what proves there is a mic, and
        # only that flips this. Born "muted", the window enabled its record button on the first
        # poll, seconds before a click could do anything.
        self.state = "waking"
        self.level = 0.0
        # The sentence they are still in the middle of. A state, not a hand-off: it stands on screen
        # until it grows or is taken down, so every poll carries it.
        self.hearing = ""
        self._typed = []      # dictation's words, waiting for the page to put them in the box
        self._send = False    # dictation said "over": the box is to be sent as it stands
        self._retract = 0     # they said "scratch that": chunks already in the box to take back out

    def drain(self):
        """Take everything the conversation and the dictation pump have said since last time."""
        for op, payload in self._feed.drain():
            if op == "state":
                self.state = payload
            elif op == "level":
                self.level = payload
            elif op == "hearing":
                self.hearing = payload
            elif op == "draft":
                self._typed.append(payload)
            elif op == "retract":
                # A chunk the page has not been handed yet is undone by never handing it over;
                # only one that already reached the box has to be taken back out of it.
                if self._typed:
                    self._typed.pop()
                else:
                    self._retract += 1
            elif op == "submit":
                self._send = True
            else:
                self.model.apply(op, payload)

    def dictated(self):
        """What dictation has done to the box since the last poll: how many chunks already in it to
        take back out, the words to type into it, and whether to send it.

        Taken, not read: handed over twice they would be typed into the box twice. The retracts
        come first because they can only ever refer to something older than the words beside them.
        """
        retract, self._retract = self._retract, 0
        typed, self._typed = self._typed, []
        send, self._send = self._send, False
        return retract, typed, send


def sessions(entries):
    """Each recorded session in the thread, as (label, where it opens).

    What the contents list offers, and what clicking one scrolls to. A session break carries no
    date of its own: the day is the last day break above it, and the time is the first thing said
    inside it, since a session with nothing said in it is not somewhere to be sent.

    Where, not which: every session break is the same dict as every other - no stamp, the same
    text - so handing back the entry meant anything looking it up by value found the first one in
    the thread, and every row in the contents led to the same place."""
    found, day = [], ""
    opening = 0 if entries else None
    for at, entry in enumerate(entries):
        role = entry["role"]
        if role == "day":
            day = entry["stamp"]
        elif role == "session":
            opening = at
        elif role in SIDES and opening is not None:
            found.append((f"{day} {entry['stamp'][:5]}".strip(), opening))
            opening = None
    return found
