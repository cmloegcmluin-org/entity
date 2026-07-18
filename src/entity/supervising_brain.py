"""Wrap the companion brain so it can start - and keep talking to - coding agents.

The Entity has no special "fleet mode": it's one program, and driving agents is just something
the user asks for in conversation. The brain says so with a directive, this wrapper acts on it, and
the user gets a short answer instead of the raw marker.

  [SUPERVISE] <where>        start a fresh agent per worktree there; everything on the
  <the task, any length>     following lines is the task that agent is given
  [TELL] <name>: <message>   say something more to an agent already running

The task travels WITH the directive on purpose. Without it the brain had no way to pass on what
the user actually asked for, so it would go and work the request out for itself first - forty-five
seconds of digging before a single word came back to him, on a request that should have been
handed straight to an agent. Relaying his requirements needs no investigation: the agent does that.

Both markers hand off to the AgentDesk and return AT ONCE. Nothing here waits on an agent: an
agent that takes twenty minutes used to hold the whole conversation for twenty minutes, which is
how the user ended up talking to a wall while it worked.
"""

import os.path
import re
from pathlib import Path

from entity.worktrees import find_worktrees, is_worktree, prepare_worktree_for

DEFAULT_TASK = (
    "You are in a git worktree. Look at the branch name and the working tree, work out what "
    "this session is meant to be doing, and continue it. Report back in a few plain sentences: "
    "what you did, and anything you need the user to decide."
)

_SUPERVISE = "[SUPERVISE]"
_TELL = "[TELL]"


def parse_supervise(reply):
    """Pull (where, task) out of a `[SUPERVISE] <where>` reply, or None if it isn't one.

    The first line names the worktree; everything after it is the task for the agent, which is how
    the user's own requirements reach it. No task lines means the caller's default task.
    """
    if _SUPERVISE not in reply:
        return None
    after = reply.split(_SUPERVISE, 1)[1].strip()
    target, _, task = after.partition("\n")
    if not target.strip():
        return None
    return target.strip(), task.strip() or None


def parse_tell(reply):
    """Pull (agent name, message) out of a `[TELL] <name>: <message>` reply, or None. The message
    runs to the end of the reply, so a correction can be as long as it needs to be."""
    if _TELL not in reply:
        return None
    after = reply.split(_TELL, 1)[1].strip()
    name, separator, message = after.partition(":")
    if not separator or not name.strip() or not message.strip():
        return None
    return name.strip(), message.strip()


def _resolve(target):
    """A worktrees directory (globbed to its sub-dirs) or explicit comma/newline-separated paths.

    A path that doesn't exist yet (the usual case - a fresh worktree named for new work) lands in
    the explicit branch, so expand ~ there too or the agent's cwd would be a bogus literal.
    """
    expanded = str(Path(target).expanduser())
    if is_worktree(expanded):
        return [expanded]  # he named ONE worktree - never fan out into its subdirectories
    if Path(expanded).is_dir():
        return find_worktrees(expanded) or [expanded]
    # expanduser only (not full Path normalization) so plain paths pass through verbatim and only ~ resolves.
    return [os.path.expanduser(part.strip()) for part in re.split(r"[,\n]", target) if part.strip()]


class SupervisingBrain:
    def __init__(self, inner, desk, *, task=DEFAULT_TASK, resolve=_resolve, prepare=prepare_worktree_for):
        self._inner = inner
        self._desk = desk
        self._task = task
        self._resolve = resolve
        self._prepare = prepare

    def respond(self, utterance):
        reply = self._inner.respond(utterance)
        told = parse_tell(reply)
        if told is not None:
            name, message = told
            if self._desk.send(name, message):
                return f"Passed that to {name}."
            return f"I don't have an agent called {name} running."
        directive = parse_supervise(reply)
        if directive is None:
            return reply
        target, task = directive
        paths = self._resolve(target)
        if not paths:
            return "I couldn't find any sessions to drive there."
        for path in paths:
            if not Path(path).exists():  # new work means a new worktree, cut from current origin/main
                self._prepare(path)
            self._desk.start(Path(path).name, path, task or self._task)
        count = len(paths)
        return f"Started {count} agent{'' if count == 1 else 's'}. I'll pass on whatever they say."

    def interrupt(self):
        self._inner.interrupt()

    def warmup(self):
        self._inner.warmup()

    def close(self):
        self._inner.close()
