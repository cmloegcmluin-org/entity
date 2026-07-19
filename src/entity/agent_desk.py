"""The agents the Entity has started, and can still talk to.

The Entity used to fire agents off and lose them: it spawned them as detached background tasks,
kept only an id, and then couldn't reach them again - four in a row went unreachable, and its own
context resets stranded the rest. It also drove them from inside a conversational turn, so while
an agent worked, the user was talking to a wall.

The desk fixes both. Each agent is a persistent session held HERE, in the process, so the handle
can't be lost to a context reset - a follow-up goes to the same agent, which remembers. And every
message is sent on a worker thread: starting an agent, or sending it one, returns at once, and
whatever the agent says back is pushed to the Outbox for the conversation to deliver at its next
natural moment. The conversation never waits on an agent again.

The roster is written to a file as agents come and go, so the brain - which can read files but
can't reach into this process - can always see who it has running. And every exchange goes into a
timestamped per-agent log the desk writes itself (log_dir/<name>.log) - the file the user tails to
watch a conversation happen, which used to be hand-authored by the brain in whatever format it
invented that day, with no timestamps.
"""

import threading
import time
from pathlib import Path

from entity.relay import notice
from entity.transcript import Transcript


class _Desked:
    """One agent and what it's doing, so the roster can say more than just a name."""

    def __init__(self, agent, cwd, task, log):
        self.agent = agent
        self.cwd = cwd
        self.task = task
        self.log = log  # the timestamped exchange log the user can tail, or None
        self.state = "starting"
        self.last_word = None  # the last thing it said back, trimmed for the roster


class AgentDesk:
    def __init__(self, outbox, *, agent_factory=None, roster_path=None, log_dir=None, clock=time.strftime):
        self._outbox = outbox
        self._factory = agent_factory or _real_agent
        self._roster_path = Path(roster_path) if roster_path else None
        self._log_dir = Path(log_dir) if log_dir else None
        self._clock = clock
        self._desked = {}
        self._lock = threading.Lock()
        self._threads = []

    def start(self, name, cwd, task):
        """Put a fresh agent on `task` in `cwd`. Returns immediately; the agent's reply arrives in
        the Outbox when it lands."""
        agent = self._factory(name, cwd, self._decide)
        with self._lock:
            self._desked[name] = _Desked(agent, cwd, task, self._open_log(name))
        self._dispatch(name, task)

    def send(self, name, message):
        """Say something more to an agent already at the desk. False if there's no such agent -
        the caller must not be told a message was delivered when it wasn't."""
        with self._lock:
            if name not in self._desked:
                return False
        self._dispatch(name, message)
        return True

    def roster(self):
        """(name, state, task) for each agent, newest state - what the roster file is written from."""
        with self._lock:
            return [(name, desked.state, desked.task) for name, desked in self._desked.items()]

    def close(self):
        with self._lock:
            desked = list(self._desked.values())
            self._desked.clear()
        for entry in desked:
            try:
                entry.agent.close()
            except Exception:
                pass  # a session that's already gone shouldn't block the rest of shutdown
        self._write_roster()

    async def _decide(self, agent, tool_name, tool_input):
        """Every tool an agent wants to use passes through here. Reading is waved through; anything
        that writes or runs is what the user cares about, and he already runs these agents
        unattended, so it goes through too - but the desk is where that policy lives if it changes."""
        return True

    def _open_log(self, name):
        return Transcript(self._log_dir / f"{name}.log") if self._log_dir is not None else None

    def _dispatch(self, name, message):
        thread = threading.Thread(target=self._carry, args=(name, message), daemon=True)
        self._threads.append(thread)
        thread.start()

    def _carry(self, name, message):
        """Deliver one message to an agent and put whatever comes back where the user will hear it."""
        with self._lock:
            entry = self._desked.get(name)
        if entry is None:  # closed out from under us
            return
        self._log(entry, message, prefix="ENTITY> ")
        self._set_state(name, "working")
        try:
            reply = entry.agent.work(message)
        except Exception as exc:  # a dead agent is news, not something to swallow
            self._log(entry, f"(died: {exc})", prefix="AGENT> ")
            self._set_state(name, "failed")
            self._outbox.push(f"The {name} agent died: {exc}")
            return
        self._log(entry, reply, prefix="AGENT> ")
        self._set_state(name, "idle", last_word=reply)
        # A notice, never the agent's own words - the full reply is in the log its tab reads.
        self._outbox.push(notice(name, reply))

    def _log(self, entry, text, *, prefix):
        if entry.log is not None:
            entry.log.write(text, prefix=prefix)

    def _set_state(self, name, state, *, last_word=None):
        with self._lock:
            entry = self._desked.get(name)
            if entry is None:
                return
            entry.state = state
            if last_word is not None:
                entry.last_word = last_word
        self._write_roster()

    def _write_roster(self):
        """The roster is a file because the brain's memory isn't reliable across a context reset,
        but a file it can read is."""
        if self._roster_path is None:
            return
        with self._lock:
            lines = [
                f"{name} | {entry.state} | {entry.cwd} | {entry.task}"
                + (f" | last: {entry.last_word[:120]}" if entry.last_word else "")
                for name, entry in self._desked.items()
            ]
        self._roster_path.parent.mkdir(parents=True, exist_ok=True)
        header = f"# agents the Entity has running, as of {self._clock('%Y-%m-%d %H:%M:%S')}\n"
        self._roster_path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")


def _real_agent(name, cwd, decide):
    # Imported here so the desk can be exercised without the SDK (and without a real agent).
    from entity.supervised_agent import SupervisedAgent

    return SupervisedAgent(name, cwd, decide)
