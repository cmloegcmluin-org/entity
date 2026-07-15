"""The bridge between the supervised agents and the user.

Each agent's approval callback (`decide`, running in that agent's background SDK loop) raises a
hand with the FleetSupervisor and then BLOCKS until the user answers - so the agent literally
waits on him. the user's side (the Entity voice loop, on the main thread) reads `waiting()`,
`pick()`s one, and `answer()`s it, which unblocks that agent. A plain threading.Event carries
the decision across threads, so it doesn't matter which event loop the agent lives on.
"""

import asyncio
import threading


def describe_request(tool_name, tool_input):
    """Render what an agent wants into a short phrase for the user."""
    tool_input = tool_input or {}
    if tool_name == "Bash":
        return f"run: {tool_input.get('command', '')}"
    if tool_name in ("Edit", "Write"):
        return f"{tool_name.lower()} {tool_input.get('file_path', 'a file')}"
    if tool_name == "Read":
        return f"read {tool_input.get('file_path', 'a file')}"
    return f"use {tool_name}"


class _Pending:
    def __init__(self):
        self._ready = threading.Event()
        self._approved = False

    def wait(self):
        self._ready.wait()
        return self._approved

    def resolve(self, approved):
        self._approved = approved
        self._ready.set()


class Fleet:
    def __init__(self, supervisor):
        self._supervisor = supervisor
        self._pending = {}
        self._lock = threading.Lock()

    async def decide(self, agent, tool_name, tool_input):
        """Agent-side: raise a hand and wait (off the event loop) for the user's yes/no."""
        pending = _Pending()
        with self._lock:
            self._pending[agent] = pending
        self._supervisor.raise_hand(agent, describe_request(tool_name, tool_input))
        return await asyncio.get_running_loop().run_in_executor(None, pending.wait)

    def waiting(self):
        return self._supervisor.waiting()

    def pick(self, agent):
        return self._supervisor.pick(agent)

    def answer(self, agent, approved):
        """the user-side: record his decision, free him, and unblock the waiting agent."""
        self._supervisor.resolve(agent)
        with self._lock:
            pending = self._pending.pop(agent, None)
        if pending is not None:
            pending.resolve(approved)
