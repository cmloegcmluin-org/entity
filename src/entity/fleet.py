"""The fleet supervisor's attention state: whose hand is up, and who you're currently with.

This is the pure decision logic behind "let me chill; speak up when an agent needs me, and
when several are ready tell me which and let me pick." It tracks outstanding needs and who
you're handling right now; it does NOT interrupt (that's the Entity only voicing `waiting()`
when you're free) and it does NOT talk to the agents (that's the SDK layer that resolves each
need by relaying your answer back).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Need:
    agent: str
    request: str  # what the agent needs from you, in plain language


class FleetSupervisor:
    def __init__(self):
        self._needs = {}  # agent -> latest request awaiting you
        self._current = None  # the agent you're handling right now, if any

    def raise_hand(self, agent, request):
        """An agent reports it needs you. Idempotent per agent — the latest request wins."""
        self._needs[agent] = request

    def waiting(self):
        """The agents currently waiting on you, excluding the one you're already handling."""
        return [Need(agent, request) for agent, request in self._needs.items() if agent != self._current]

    @property
    def is_free(self):
        return self._current is None

    @property
    def current(self):
        return self._current

    def pick(self, agent):
        """You choose which waiting agent to handle now (any of them, not just the oldest)."""
        need = Need(agent, self._needs[agent])
        self._current = agent
        return need

    def resolve(self, agent):
        """You've answered this agent — drop its need and (if it was the one you had) free you up."""
        self._needs.pop(agent, None)
        if self._current == agent:
            self._current = None
