"""Wrap the companion brain so it can start - and keep talking to - coding agents.

The Entity has no special "fleet mode": it's one program, and driving agents is just something
the user asks for in conversation. The brain says so with a directive, this wrapper acts on it, and
the user gets a short answer instead of the raw marker.

  [SUPERVISE] <where>        start a fresh agent per worktree there
  [TELL] <name>: <message>   say something more to an agent already running

Both hand off to the AgentDesk and return AT ONCE. Nothing here waits on an agent: an agent that
takes twenty minutes used to hold the whole conversation for twenty minutes, which is how the user
ended up talking to a wall while it worked.
"""

import os.path
import re
from pathlib import Path

from entity.worktrees import find_worktrees, prepare_worktree_for

DEFAULT_TASK = (
    "You are in a git worktree. Look at the branch name and the working tree, work out what "
    "this session is meant to be doing, and continue it. Report back in a few plain sentences: "
    "what you did, and anything you need the user to decide."
)

_SUPERVISE = "[SUPERVISE]"
_TELL = "[TELL]"


def parse_supervise(reply):
    """Pull the target out of a `[SUPERVISE] <where>` reply, or None if it isn't one."""
    if _SUPERVISE not in reply:
        return None
    after = reply.split(_SUPERVISE, 1)[1].strip()
    return after.split("\n", 1)[0].strip() or None


def parse_tell(reply):
    """Pull (agent name, message) out of a `[TELL] <name>: <message>` reply, or None."""
    if _TELL not in reply:
        return None
    after = reply.split(_TELL, 1)[1].strip().split("\n", 1)[0]
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
        target = parse_supervise(reply)
        if target is None:
            return reply
        paths = self._resolve(target)
        if not paths:
            return "I couldn't find any sessions to drive there."
        for path in paths:
            if not Path(path).exists():  # new work means a new worktree, cut from current origin/main
                self._prepare(path)
            self._desk.start(Path(path).name, path, self._task)
        count = len(paths)
        return f"Started {count} agent{'' if count == 1 else 's'}. I'll pass on whatever they say."

    def interrupt(self):
        self._inner.interrupt()

    def warmup(self):
        self._inner.warmup()

    def close(self):
        self._inner.close()
