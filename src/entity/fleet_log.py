"""A readable transcript of a fleet-driving session, in the labelled ENTITY/AGENT format.

the user can't watch the agents' screens, so besides hearing them by voice he wants a durable
record he can scroll back through - labelled, with a timestamp on every line, written from the
start of the session (not reconstructed after the fact). `FleetLog` is the labelling; the
stamping, locking and file handling live in `Transcript`, shared with the conversation's own
session record. `NullFleetLog` is the do-nothing stand-in for runs (and tests) with no file.
"""

from datetime import datetime

from entity.transcript import Transcript


class FleetLog:
    def __init__(self, path, *, clock=datetime.now, timefmt="%H:%M:%S"):
        self._transcript = Transcript(path, clock=clock, timefmt=timefmt)

    def entity(self, text):
        """Something the Entity said or did, on the user's behalf."""
        self._transcript.write(text, prefix="ENTITY: ")

    def agent(self, name, text):
        """Something the named agent reported back."""
        self._transcript.write(text, prefix=f"AGENT {name}: ")


class NullFleetLog:
    """Swallows every entry - used when a session shouldn't leave a file behind."""

    def entity(self, text):
        pass

    def agent(self, name, text):
        pass
