"""Wrap the companion brain so it can start - and keep talking to - coding agents.

The Entity has no special "fleet mode": it's one program, and driving agents is just something
asked for in conversation. The brain says so with a directive, this wrapper acts on it, and the
user hears whatever the brain had to say - never the raw marker.

  [SUPERVISE] <where>        start a fresh agent per worktree there; everything on the
  <the task, any length>     following lines is the task that agent is given
  [TELL] <name>: <message>   say something more to an agent already running
  [MODEL] <what he said>     put the NEXT agent on that model/effort ("fable max", "opus high")
  [IMPROVE] <one line>       file a self-improvement that was asked for - it lands in the
                             profile's Enhancements section, which the window shows live
                             (one line per item, and every one of them is filed)

Whatever the brain writes AROUND a directive is what the user hears; the marker never reaches them.
That matters because a directive used to consume the whole reply, so any turn that filed or
dispatched something answered the user with a canned "Filed." or "Passed that to <agent>." and
their actual question went unanswered. With nothing written around it, a SUCCESS says nothing at
all: the spoken "Got it." at the top of the turn already confirmed receipt, and they counted the
stock phrases per turn and asked for exactly one. Only failures always speak.

The task travels WITH the directive on purpose. Without it the brain had no way to pass on what
was actually asked for, so it would go and work the request out for itself first - forty-five
seconds of digging before a single word came back, on a request that should have been handed
straight to an agent. Relaying requirements needs no investigation: the agent does that.

The agent markers hand off to the AgentDesk and return AT ONCE. Nothing here waits on an agent: an
agent that takes twenty minutes used to hold the whole conversation for twenty minutes, which
left the user talking to a wall while it worked.
"""

import os.path
import re
from pathlib import Path

from entity.memory import append_enhancement
from entity.models import resolve as resolve_model
from entity.worktrees import find_worktrees, is_worktree, prepare_worktree_for

DEFAULT_TASK = (
    "You are in a git worktree. Look at the branch name and the working tree, work out what "
    "this session is meant to be doing, and continue it. Report back in a few plain sentences: "
    "what you did, and anything you need the user to decide."
)

_SUPERVISE = "[SUPERVISE]"
_TELL = "[TELL]"
_IMPROVE = "[IMPROVE]"
_MODEL = "[MODEL]"


def parse_model(reply):
    """Pull `[MODEL] <what he said>` out of a reply, or None."""
    if _MODEL not in reply:
        return None
    line = reply.split(_MODEL, 1)[1].strip().split("\n", 1)[0].strip()
    return line or None


# What the brain said to THEM in a reply that also carried a directive, or "" if it said nothing.
#
# A directive used to consume the whole reply and hand back a canned line, so any turn that filed or
# dispatched something answered whatever they had asked with "Filed." or "Passed that to <agent>." -
# eight of them in one session. Their all-caps demand to be shown the work running got "Passed that
# to drive-native-gdoc-export." and nothing else. The question wasn't deprioritized; the answer was
# written and then deleted.


def _spoken_before(reply, marker):
    """For a directive whose payload runs to the end of the reply: whatever came before it."""
    return reply.partition(marker)[0].strip()


def _spoken_around_improvements(reply):
    """An `[IMPROVE]` item is one line, so every OTHER line was meant for them."""
    return "\n".join(line for line in reply.splitlines() if _IMPROVE not in line).strip()


def _plain(reply):
    """A reply with no directive in it, safe to say aloud.

    Usually that is the reply itself. But a marker it FUMBLED - no colon after the agent name, an
    empty target, a blank item - parses as no directive at all and used to fall through to here and
    be read out bracket and all: "I don't appreciate how you're speaking to me in code. We're
    supposed to be having a conversation as human like Entities." So the marker lines are dropped,
    and what's left is what he hears. If that leaves nothing, say the attempt failed rather than
    going quiet - silence would leave him believing something was filed or sent when it wasn't.
    """
    if not any(marker in reply for marker in (_SUPERVISE, _TELL, _IMPROVE, _MODEL)):
        return reply
    kept = "\n".join(
        line for line in reply.splitlines()
        if not any(marker in line for marker in (_SUPERVISE, _TELL, _IMPROVE, _MODEL))
    ).strip()
    return kept or "I fumbled that one - it didn't go through. Say it again?"


def parse_improvements(reply):
    """Every `[IMPROVE] <one line>` in the reply, in the order they were written.

    All of them, not just the first: asked for two tickets it filed one, and he had to notice the
    gap and ask again for something he had already asked for."""
    return [
        item
        for item in (line.partition(_IMPROVE)[2].strip() for line in str(reply).splitlines())
        if item
    ]


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
        return [expanded]  # ONE worktree was named - never fan out into its subdirectories
    if Path(expanded).is_dir():
        return find_worktrees(expanded) or [expanded]
    # expanduser only (not full Path normalization) so plain paths pass through verbatim and only ~ resolves.
    return [os.path.expanduser(part.strip()) for part in re.split(r"[,\n]", target) if part.strip()]


class SupervisingBrain:
    def __init__(self, inner, desk, *, task=DEFAULT_TASK, resolve=_resolve, prepare=prepare_worktree_for,
                 file_enhancement=append_enhancement):
        self._inner = inner
        self._desk = desk
        self._task = task
        self._resolve = resolve
        self._prepare = prepare
        self._file_enhancement = file_enhancement

    def respond(self, utterance):
        reply = self._inner.respond(utterance)
        # A success with no aside returns "" - said aloud, a canned confirmation was one more stock
        # phrase on top of the ack that already told him he was heard: "this could all be collapsed
        # into a single 'Got it.'". Only successes may be silent; every failure below still speaks.
        improvements = parse_improvements(reply)
        if improvements:
            for item in improvements:
                self._file_enhancement(item)
            return _spoken_around_improvements(reply)
        wanted = parse_model(reply)
        if wanted is not None:
            choice = resolve_model(wanted)
            if choice is None:  # nothing in it named a model or an effort - say so, don't guess
                return f"I didn't catch a model in that. Still on {self._desk.running_on()}."
            said = self._desk.choose(*choice)
            return _spoken_before(reply, _MODEL) or f"Next agent goes on {said}."
        told = parse_tell(reply)
        if told is not None:
            name, message = told
            if not self._desk.send(name, message):
                # Never what it said alongside: that was written expecting the message to land, so
                # speaking it would report a delivery that did not happen.
                return f"I don't have an agent called {name} running."
            return _spoken_before(reply, _TELL)
        directive = parse_supervise(reply)
        if directive is None:
            return _plain(reply)
        target, task = directive
        paths = self._resolve(target)
        if not paths:
            return "I couldn't find any sessions to drive there."
        for path in paths:
            if not Path(path).exists():  # new work means a new worktree, cut from current origin/main
                self._prepare(path)
            self._desk.start(Path(path).name, path, task or self._task)
        return _spoken_before(reply, _SUPERVISE)

    def interrupt(self):
        self._inner.interrupt()

    def warmup(self):
        self._inner.warmup()

    def close(self):
        self._inner.close()
