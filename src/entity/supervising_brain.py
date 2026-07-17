"""Wrap the companion brain so it can start driving a fleet from ordinary conversation.

The Entity has no special "fleet mode" - it's one program. When the user tells it (in normal
talk) to resume/drive some Claude coding sessions, the brain replies with a `[SUPERVISE] <where>`
directive; this wrapper catches that, launches supervised agents for those worktrees, manages
them through the same voice, and then answers the user with a summary instead of the raw directive.
General on purpose: `<where>` is whatever worktrees he named, not anything Notecraft-specific.
"""

import re
from pathlib import Path

from entity.fleet_session import find_worktrees, supervise

_MARKER = "[SUPERVISE]"


def parse_supervise(reply):
    """Pull the target out of a `[SUPERVISE] <where>` reply, or None if it isn't one."""
    if _MARKER not in reply:
        return None
    after = reply.split(_MARKER, 1)[1].strip()
    return after.split("\n", 1)[0].strip() or None


def _resolve(target):
    """A worktrees directory (globbed to its sub-dirs) or explicit comma/newline-separated paths."""
    expanded = str(Path(target).expanduser())
    if Path(expanded).is_dir():
        return find_worktrees(expanded) or [expanded]
    return [part.strip() for part in re.split(r"[,\n]", target) if part.strip()]


class SupervisingBrain:
    def __init__(self, inner, io, *, model="sonnet", supervise_fn=supervise, resolve=_resolve, make_log=None):
        self._inner = inner
        self._io = io
        self._model = model
        self._supervise = supervise_fn
        self._resolve = resolve
        self._make_log = make_log  # target -> a FleetLog for this session's transcript (or None)

    def respond(self, utterance):
        reply = self._inner.respond(utterance)
        target = parse_supervise(reply)
        if target is None:
            return reply
        paths = self._resolve(target)
        if not paths:
            return "I couldn't find any sessions to drive there."
        log = self._make_log(target) if self._make_log else None
        reports = self._supervise(paths, self._io, model=self._model, log=log)
        return f"Done. I supervised {len(reports)} agents."

    def warmup(self):
        self._inner.warmup()

    def close(self):
        self._inner.close()
