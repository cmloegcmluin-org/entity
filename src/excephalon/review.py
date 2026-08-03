"""The memory inbox's nudge: "this list is an inbox, and I'm an inbox-0 kind of guy."

A memory only earns its place by changing something, so each one awaits his verdict - keep it,
drop it, or turn it into an instruction. Excephalon raises them ITSELF, but only in genuine
downtime: no agent at the desk working, and the conversation quiet for a few minutes - because
an inbox that interrupts work is worse than one that waits. One memory per nudge, each raised at
most once per session, and never two nudges inside the gap, so the review can never become the
nagging every stock phrase was deleted over.
"""

import threading
import time


class MemoryNudger:
    def __init__(self, events, *, memories, fleet_idle, quiet_for,
                 clock=time.monotonic, settle=180.0, gap=600.0):
        self._events = events          # ("memory", "memory", fact) -> the narrator words it
        self._memories = memories      # -> list of remembered facts, live from the store
        self._fleet_idle = fleet_idle  # -> True when no agent is working
        self._quiet_for = quiet_for    # -> seconds since the conversation last moved
        self._clock = clock
        self._settle = settle
        self._gap = gap
        self._raised = set()           # facts already offered this session; once each
        self._last = None

    def poll_once(self):
        if not self._fleet_idle() or self._quiet_for() < self._settle:
            return
        if self._last is not None and self._clock() - self._last < self._gap:
            return
        for fact in self._memories():
            if fact not in self._raised:
                self._raised.add(fact)
                self._last = self._clock()
                self._events("memory", "memory", fact)
                return

    def run(self, *, stop, every=30.0, sleep=None):
        wait = sleep or (lambda seconds: stop.wait(seconds))
        while not stop.is_set():
            try:
                self.poll_once()
            except Exception:
                pass  # a broken nudge must never take the session down
            wait(every)

    def start(self, stop):
        thread = threading.Thread(target=self.run, kwargs={"stop": stop}, daemon=True)
        thread.start()
        return thread
