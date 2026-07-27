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

import json
import threading
import time
from pathlib import Path

from entity.delivery import Delivery, DeliveryError
from entity.models import DEFAULT_EFFORT, DEFAULT_MODEL, describe
from entity.relay import notice
from entity.steps import SAID, render
from entity.tailing import archive_dir
from entity.transcript import AGENT_DID, AGENT_SAID, ENTITY_SAID, Transcript


# Attached to every task the desk hands out, because asking for it each time did not hold: the
# brain wrote it into some dispatches and not others, and the round it forgot cost a whole review -
# work shown off a stale branch reads as though features that had already merged were missing. The
# second rule earned its place the same way: "verification" came back as "run pytest", and the
# user had to say - again - that green tests are not their eyes.
STANDING_RULE = (
    "\n\nStanding rules, from the person this work is for, and they hold however the task above "
    "is worded. One: before you present ANY branch, build or running instance for them to look "
    "at, first `git fetch origin` and rebase your branch onto the latest `origin/main`, then "
    "re-run the tests on the rebased commit - shown off a stale branch, work that other people "
    "have already merged looks to them like it has gone missing. Two: when your work is done, "
    "'ready for review' means THEY can SEE it working with their own eyes and mouse - stand up a "
    "live instance of the app on its own port with its own scratch data, apart from their real "
    "one, and report the exact click-by-click steps to watch the new behavior happen. Never "
    "offer 'run the tests' as their verification: green tests are your evidence, not theirs, "
    "and they will send it back. Three: engineering discipline is not optional - read the "
    "repo's CLAUDE.md before you start and follow it; test-drive every change (one failing "
    "test, the minimum code to pass it, refactor, again); run the project's full test suite "
    "green before anything lands; land through the repo's own process - a repo with a PR merge "
    "queue means push a branch, open a PR, enqueue it, and watch it to actually merged, never a "
    "direct push to a shared main - and leave everything you touched cleaner than you found it."
)


def _one_line(text, limit=160):
    """A task or a last word as one digest-sized line: its first line, capped."""
    line = str(text).strip().splitlines()[0] if str(text).strip() else ""
    return line if len(line) <= limit else line[:limit].rstrip() + "…"


# What a restarted Entity says to an agent it found recorded mid-task. The resumed session
# remembers everything, so the message is a nudge, not a re-briefing.
CONTINUE_AFTER_RESTART = (
    "Entity restarted while you were mid-task. Your session was resumed, so everything you knew "
    "still holds. Pick up exactly where you left off and finish; if you had in fact finished, "
    "report where things stand."
)

# Sent by the desk itself the moment a verdict is recorded - after the user has spoken, what
# remains is mechanical, and mechanical steps are not left to anyone's memory.
APPROVED_LAND_IT = (
    "The user looked at what you presented and signed off. Land it now: push your branch, open "
    "the PR, enqueue it on the merge queue, and see it through - then report that it merged, or "
    "exactly what stopped it."
)
REJECTED_TRY_AGAIN = (
    "The user looked at what you presented and rejected it: {feedback}\n"
    "Address their feedback and present again when it is ready for their eyes."
)


class _Desked:
    """One agent and what it's doing, so the roster can say more than just a name."""

    def __init__(self, agent, cwd, task, log, *, model, effort, delivery=None):
        self.agent = agent
        self.cwd = cwd
        self.task = task
        self.log = log  # the timestamped exchange log the user can tail, or None
        self.model = model  # what it was started on, so a revival can put it back on the same
        self.effort = effort
        self.delivery = delivery or Delivery()  # where this work stands in the review loop
        self.state = "starting"
        self.last_heard = None  # when it last said anything at all, step or reply
        self.last_word = None  # the last thing it said back, trimmed for the roster


class AgentDesk:
    def __init__(self, outbox, *, agent_factory=None, roster_path=None, log_dir=None,
                 monitor=None, clock=time.strftime, events=None, run=None, state_path=None,
                 law_path=None):
        from entity.worktrees import run_hidden

        self._run = run or run_hidden  # how retire removes a finished agent's worktree
        # What happened - finished, died - goes to the events sink as (kind, agent, report); the
        # narrator words it in the brain's own voice. Undirected, the desk speaks the old way:
        # a capped notice (or the death line) straight to the outbox.
        self._events = events or self._plain_notices
        self._outbox = outbox
        self._factory = agent_factory or _real_agent
        self._roster_path = Path(roster_path) if roster_path else None
        self._state_path = Path(state_path) if state_path else None  # the fleet's survival record
        self._law_path = Path(law_path) if law_path else None  # the machine-wide engineering law
        self._log_dir = Path(log_dir) if log_dir else None
        # Where a finished agent's log goes to rest - the fleet's one archive (see tailing).
        self._archive_dir = archive_dir(self._log_dir) if self._log_dir else None
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
            self._desked[name] = _Desked(agent, cwd, task, self._open_log(name),
                                         model=self._model, effort=self._effort)
        self._dispatch(name, task + STANDING_RULE + self._law_note())

    def _law_note(self):
        """The machine-wide engineering law, pointed at rather than pasted: one source, no size
        ceiling, and each agent reads the CURRENT text, never a copy staled by Entity's uptime.
        Silent when the file isn't there - a fresh machine must not send agents chasing it."""
        if self._law_path is None or not self._law_path.exists():
            return ""
        return (f"\n\nThe user's machine-wide engineering law is in {self._law_path} - read "
                "that file before you begin, and follow it as strictly as this repo's own "
                "CLAUDE.md.")

    def revive(self):
        """Reopen every agent the last process recorded, each resumed on its old session.

        "Obviously the agent processes must be independent of Entity. I close it and reopen it
        constantly" - and a restart used to strand the whole fleet. An agent recorded mid-task is
        told to pick back up; one that was idle is reattached and left in peace. An entry with no
        session id was never heard from, so there is nothing to resume - it is skipped. Returns
        the names brought back."""
        if self._state_path is None or not self._state_path.exists():
            return []
        try:
            saved = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []  # an unreadable record must not stop the app from starting
        revived = []
        for entry in saved:
            name, session = entry.get("name"), entry.get("session_id")
            if not name or not session:
                continue
            model = entry.get("model") or self._model
            effort = entry.get("effort") or self._effort
            agent = self._factory(name, entry.get("cwd"), self._decide,
                                  model=model, effort=effort, resume=session)
            with self._lock:
                desked = _Desked(agent, entry.get("cwd"), entry.get("task", ""),
                                 self._open_log(name), model=model, effort=effort,
                                 delivery=Delivery(entry.get("delivery") or "building",
                                                   entry.get("steps")))
                desked.state = "idle"
                self._desked[name] = desked
            revived.append(name)
            if entry.get("state") in ("starting", "working"):
                self._dispatch(name, CONTINUE_AFTER_RESTART)
        self._persist()
        return revived

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

    def present(self, name, steps):
        """Record that `name`'s work is standing up for the user's eyes, with the steps to see it.

        Refused for an agent mid-turn: the steps come from its report, so marking before it has
        reported would present a thing that does not exist yet. Raises DeliveryError with the
        reason - the caller owes the brain that sentence, not a silent no."""
        with self._lock:
            entry = self._desked.get(name)
            if entry is None:
                raise DeliveryError(f"no agent called {name} is at the desk")
            if entry.state in ("starting", "working"):
                raise DeliveryError(f"{name} hasn't finished its turn yet - wait for its report")
            entry.delivery.present(steps)
        self._persist()

    def verdict(self, name, approved, feedback=""):
        """Record the user's verdict on presented work, and set the mechanical consequence going:
        approval sends the agent to land it, rejection carries the feedback back. The Delivery
        refuses a verdict on work never presented - the loop's whole point."""
        with self._lock:
            entry = self._desked.get(name)
            if entry is None:
                raise DeliveryError(f"no agent called {name} is at the desk")
            entry.delivery.verdict(approved)
        self._persist()
        if approved:
            self._dispatch(name, APPROVED_LAND_IT)
        else:
            self._dispatch(name, REJECTED_TRY_AGAIN.format(feedback=feedback))

    def delivery_stage(self, name):
        """Where `name`'s work stands - what the narrator asks before wording a finished turn."""
        with self._lock:
            entry = self._desked.get(name)
            return entry.delivery.stage if entry is not None else None

    def task_of(self, name):
        """What `name` was put on - the first thing a senior read of its situation needs."""
        with self._lock:
            entry = self._desked.get(name)
            return entry.task if entry is not None else None

    def recent_log(self, name, limit=3000):
        """The tail of an agent's exchange log - the situation as it actually unfolded, for the
        foreman's senior read. The tail and not the whole file, because a day-long exchange would
        drown the situation it ends on. Empty when there is nothing to read."""
        if self._log_dir is None:
            return ""
        try:
            text = (self._log_dir / f"{name}.log").read_text(encoding="utf-8")
        except OSError:
            return ""
        return text[-limit:]

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
                + (f" - {entry.delivery.describe()}" if entry.delivery.describe() else "")
                + (f" - last said: {_one_line(entry.last_word)}" if entry.last_word else "")
                for name, entry in self._desked.items()
            ]
        return "\n".join(lines) or "No agents running."

    def retire(self, name):
        """Wrap a finished agent up in one gesture: close its tab (the log moves into the archive),
        let the session go, and remove its worktree.

        "It should probably archive the agent log... and always do stuff like archive the Claude
        session and worktree" - three chores nobody should have to name separately. False when
        there is nothing to retire, or the agent is still live - closing a live agent's tab would
        drop the user's view into work still happening. An agent the desk never had (yesterday's,
        before a restart) is just its leftover log, and the move alone closes it. A worktree that
        refuses removal (dirty, locked) is left for a maintenance sweep - the wrap-up itself never
        fails over it."""
        with self._lock:
            entry = self._desked.get(name)
            if entry is not None and entry.state not in ("idle", "failed"):
                return False  # starting or working - live either way, and a live tab stays up
        log = self._log_dir / f"{name}.log" if self._log_dir is not None else None
        if entry is None and (log is None or not log.exists()):
            return False
        if log is not None and log.exists() and self._archive_dir is not None:
            self._archive_dir.mkdir(parents=True, exist_ok=True)
            log.replace(self._archive_dir / log.name)
        if entry is not None:
            with self._lock:
                self._desked.pop(name, None)
            try:
                entry.agent.close()  # the session first: nothing may hold the worktree open
            except Exception:
                pass  # the session may already be gone; the wrap-up carries on
            try:
                self._run(["git", "-C", entry.cwd, "worktree", "remove", entry.cwd], check=True)
            except Exception:
                pass  # dirty or locked: the sweep's business later, not a failed retirement
            self._persist()
        return True

    def close(self):
        # The survival record is written BEFORE the fleet is let go: it is what the next process
        # revives from, so shutdown must leave it showing the fleet as it stood, not empty.
        self._write_state()
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
            self._events("died", name, str(exc))
            return
        self._set_state(name, "idle", last_word=reply)
        self._finished(name)
        self._events("finished", name, reply)

    def _plain_notices(self, kind, agent, report):
        """The undirected default: what the desk always said, straight to the outbox. A notice,
        never the agent's own words - the full reply is in the log its tab reads. Named, so that
        several landing together can be read out by name for one of them to be picked."""
        if kind == "died":
            self._outbox.push(f"The {agent} agent died: {report}", about=agent)
        else:
            self._outbox.push(notice(agent, report), about=agent)

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
        self._persist()

    def _persist(self):
        self._write_roster()
        self._write_state()

    def _write_state(self):
        """Everything a fresh process needs to reattach: who, where, on which session. JSON,
        because this one is read back by code (`revive`), not by the brain."""
        if self._state_path is None:
            return
        with self._lock:
            record = [
                {"name": name, "cwd": entry.cwd, "task": entry.task,
                 "session_id": getattr(entry.agent, "session_id", None),
                 "state": entry.state, "model": entry.model, "effort": entry.effort,
                 "delivery": entry.delivery.stage, "steps": entry.delivery.steps}
                for name, entry in self._desked.items()
            ]
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps(record, indent=2), encoding="utf-8")

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


def _real_agent(name, cwd, decide, *, model=DEFAULT_MODEL, effort=DEFAULT_EFFORT, resume=None):
    # Imported here so the desk can be exercised without the SDK (and without a real agent).
    from entity.supervised_agent import SupervisedAgent

    return SupervisedAgent(name, cwd, decide, model=model, effort=effort, resume=resume)
