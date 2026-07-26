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

from entity.models import DEFAULT_EFFORT, DEFAULT_MODEL, describe
from entity.relay import notice
from entity.steps import SAID, render
from entity.transcript import AGENT_DID, AGENT_SAID, ENTITY_SAID, Transcript


# Attached to every task the desk hands out, because asking for it each time did not hold: the
# brain wrote it into some dispatches and not others, and the round it forgot cost a whole review -
# work shown off a stale branch reads as though features that had already merged were missing.
STANDING_RULE = (
    "\n\nStanding rule, from the person this work is for, and it holds however the task above is "
    "worded: before you present ANY branch, build or running instance for them to look at, first "
    "`git fetch origin` and rebase your branch onto the latest `origin/main`, then re-run the "
    "tests on the rebased commit. Shown off a stale branch, work that other people have already "
    "merged looks to them like it has gone missing, and they have lost a review round to that."
)


def _one_line(text, limit=160):
    """A task or a last word as one digest-sized line: its first line, capped."""
    line = str(text).strip().splitlines()[0] if str(text).strip() else ""
    return line if len(line) <= limit else line[:limit].rstrip() + "…"


class _Desked:
    """One agent and what it's doing, so the roster can say more than just a name."""

    def __init__(self, agent, cwd, task, log):
        self.agent = agent
        self.cwd = cwd
        self.task = task
        self.log = log  # the timestamped exchange log the user can tail, or None
        self.state = "starting"
        self.last_heard = None  # when it last said anything at all, step or reply
        self.last_word = None  # the last thing it said back, trimmed for the roster


class AgentDesk:
    def __init__(self, outbox, *, agent_factory=None, roster_path=None, log_dir=None,
                 monitor=None, clock=time.strftime):
        self._outbox = outbox
        self._factory = agent_factory or _real_agent
        self._roster_path = Path(roster_path) if roster_path else None
        self._log_dir = Path(log_dir) if log_dir else None
        # Who is actually alive. Silence used to be measured off the agent-inbox FILENAMES, which
        # know nothing about agents: a note Entity wrote itself became an "agent" that then went
        # quiet, and a working agent that hadn't written to its inbox looked dead. Both were
        # reported to the user as fact, and both were denied on the spot by someone reading the log.
        self._monitor = monitor
        # Which model their agents run on, and how hard they are told to think. Held HERE because
        # they change it by asking - and because an agent's session is fixed at birth, so a change
        # governs the next one started, never one already working. It was hardcoded and invisible;
        # they had to ask what their agents were running and could not be told (see entity.models).
        self._model, self._effort = DEFAULT_MODEL, DEFAULT_EFFORT
        self._clock = clock
        self._desked = {}
        self._lock = threading.Lock()
        self._threads = []

    def start(self, name, cwd, task):
        """Put a fresh agent on `task` in `cwd`. Returns immediately; the agent's reply arrives in
        the Outbox when it lands.

        The standing rule rides along with the task itself - not with every later message, since
        the session keeps it, and repeating it would be most of what the agent's tab is made of."""
        agent = self._factory(name, cwd, self._decide, model=self._model, effort=self._effort)
        with self._lock:
            self._desked[name] = _Desked(agent, cwd, task, self._open_log(name))
        self._dispatch(name, task + STANDING_RULE)

    def choose(self, model=None, effort=None):
        """Put the NEXT agent on this model, at this effort, and say what it will be. Either half
        left out keeps what was there, because they say one or the other as often as both."""
        self._model = model or self._model
        self._effort = effort or self._effort
        return describe(self._model, self._effort)

    def running_on(self):
        """What a fresh agent would be started on right now."""
        return describe(self._model, self._effort)

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

    def digest(self):
        """The fleet as a few plain lines, for handing to a brain at the top of a turn.

        "How's it going?" used to send the brain off to read the roster file with its own tools -
        half a minute of dead air for state this process already held in memory. The digest is
        that state as text, so a status question is answerable in the breath it was asked."""
        with self._lock:
            lines = [
                f"{name}: {entry.state}"
                + (f", last heard {entry.last_heard}" if entry.last_heard else "")
                + f" - task: {_one_line(entry.task)}"
                + (f" - last said: {_one_line(entry.last_word)}" if entry.last_word else "")
                for name, entry in self._desked.items()
            ]
        return "\n".join(lines) or "No agents running."

    def retire(self, name):
        """Close an agent's tab: move its log into closed/ and let a finished session go.

        False when there is nothing to retire, or the agent is still WORKING - closing a live
        agent's tab would drop the user's view into work still happening. An agent the desk never
        had (yesterday's, before a restart) is just its leftover log, and the move alone closes it.
        """
        with self._lock:
            entry = self._desked.get(name)
            if entry is not None and entry.state == "working":
                return False
        log = self._log_dir / f"{name}.log" if self._log_dir is not None else None
        if entry is None and (log is None or not log.exists()):
            return False
        if log is not None and log.exists():
            closed = self._log_dir / "closed"
            closed.mkdir(parents=True, exist_ok=True)
            log.replace(closed / log.name)
        if entry is not None:
            with self._lock:
                self._desked.pop(name, None)
            try:
                entry.agent.close()
            except Exception:
                pass  # the session may already be gone; the tab is what was asked about
            self._write_roster()
        return True

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
        that writes or runs is what the user cares about, and they already run these agents
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
        self._log(entry, message, prefix=ENTITY_SAID)
        self._set_state(name, "working")
        self._alive(name)  # it has work in flight from this moment; the silence clock starts here
        try:
            # Everything the agent streams back goes to the log as it happens, so their tab shows
            # the agent working rather than an empty file that reads exactly like a dead one.
            reply = entry.agent.work(message, on_message=lambda msg: self._heard(name, msg))
        except Exception as exc:  # a dead agent is news, not something to swallow
            self._log(entry, f"(died: {exc})", prefix=AGENT_SAID)
            self._set_state(name, "failed")
            self._finished(name)  # already announced as dead; don't also announce it as quiet later
            self._outbox.push(f"The {name} agent died: {exc}", about=name)
            return
        self._set_state(name, "idle", last_word=reply)
        self._finished(name)
        # A notice, never the agent's own words - the full reply is in the log its tab reads. Named,
        # so that several landing together can be read out by name for one of them to be picked.
        self._outbox.push(notice(name, reply), about=name)

    def _heard(self, name, message):
        """One message back from an agent - what it said AND what it did - logged as it arrives."""
        with self._lock:
            entry = self._desked.get(name)
        if entry is not None:
            for kind, text in render(message):
                self._log(entry, text, prefix=AGENT_SAID if kind == SAID else AGENT_DID)
                if kind == SAID:
                    entry.last_word = text[:120]  # the roster carries its words, not its machinery
            entry.last_heard = self._clock("%Y-%m-%d %H:%M:%S")
            self._alive(name)  # the same signal the roster records - any message is a sign of life

    def _alive(self, name):
        if self._monitor is not None:
            self._monitor.checked_in(name)

    def _finished(self, name):
        if self._monitor is not None:
            self._monitor.done(name)

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
                f"{name} | {entry.state} | last heard {entry.last_heard or 'not yet'} | "
                f"{entry.cwd} | {entry.task}"
                + (f" | last: {entry.last_word[:120]}" if entry.last_word else "")
                for name, entry in self._desked.items()
            ]
        self._roster_path.parent.mkdir(parents=True, exist_ok=True)
        header = f"# agents the Entity has running, as of {self._clock('%Y-%m-%d %H:%M:%S')}\n"
        self._roster_path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")


def _real_agent(name, cwd, decide, *, model=DEFAULT_MODEL, effort=DEFAULT_EFFORT):
    # Imported here so the desk can be exercised without the SDK (and without a real agent).
    from entity.supervised_agent import SupervisedAgent

    return SupervisedAgent(name, cwd, decide, model=model, effort=effort)
