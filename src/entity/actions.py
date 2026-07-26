"""What the brain can DO, as typed tools instead of code-words in its speech.

It used to act by writing marker phrases - [SUPERVISE], [TELL] - into its own conversational
reply, for a scanner to fish back out. That made its speech double as its control panel, and both
jobs suffered: a marker the scanner missed was read aloud ("I don't appreciate how you're speaking
to me in code"), a marker typed slightly wrong did nothing and told no one, and a status question
was sometimes answered by *dispatching* because writing the phrase was the only verb it had. A
typed tool call cannot be half-written, cannot leak into the voice, and returns a result the model
has to look at.

The tools run in-process (an SDK MCP server), so acting is one round trip with no subprocess, and
every one of them returns in well under a second - the desk does agent work on its own threads.
"""

import os.path
import re
from pathlib import Path

from claude_agent_sdk import create_sdk_mcp_server, tool

from entity.delivery import DeliveryError
from entity.memory import append_enhancement
from entity.models import resolve as resolve_model
from entity.worktrees import find_worktrees, is_worktree, prepare_worktree_for

SERVER = "entity"

# The names the model calls, and the only tools its options allow: the conversational brain has no
# Bash, no Read, no way to wander a repo mid-turn - investigation belongs to the agents it starts.
TOOL_NAMES = tuple(f"mcp__{SERVER}__{name}"
                   for name in ("start_agent", "tell_agent", "set_next_agent_model",
                                "file_improvement", "close_agent_tab", "mark_ready",
                                "record_verdict"))

DEFAULT_TASK = (
    "You are in a git worktree. Look at the branch name and the working tree, work out what "
    "this session is meant to be doing, and continue it. Report back in a few plain sentences: "
    "what you did, and anything you need the user to decide."
)


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


def fleet_actions(desk, *, file_enhancement=append_enhancement, resolve=_resolve,
                  prepare=prepare_worktree_for, default_task=DEFAULT_TASK):
    """The action tools, wired to this desk: (server config for the options, the tools themselves).

    The tools come back too so tests can drive the handlers directly - the server config is an
    opaque box once built."""

    @tool("start_agent", "Start a fresh coding agent working on a task. `path` is the absolute "
          "path of the worktree to work in (a new path gets a new worktree cut from current "
          "origin/main). `task` is the user's requirements, passed on faithfully and completely - "
          "every constraint they stated.", {"path": str, "task": str})
    async def start_agent(args):
        paths = resolve(str(args["path"]))
        if not paths:
            return _say("I couldn't find any sessions to drive there.")
        for path in paths:
            if not Path(path).exists():  # new work means a new worktree, cut from current origin/main
                prepare(path)
            desk.start(Path(path).name, path, str(args.get("task") or default_task))
        names = ", ".join(Path(path).name for path in paths)
        return _say(f"Started {names} on {desk.running_on()}.")

    @tool("tell_agent", "Say something more to an agent already running - a correction, an answer, "
          "a follow-up. `name` is the agent's name from the fleet briefing.",
          {"name": str, "message": str})
    async def tell_agent(args):
        name = str(args["name"]).strip()
        if not desk.send(name, str(args["message"])):
            return _say(f"No agent called {name} is running - check the fleet briefing.")
        return _say(f"Delivered to {name}.")

    @tool("set_next_agent_model", "Set which model and effort the NEXT agent starts on, from the "
          "user's words ('fable on max', 'back to opus'). Agents already working keep the model "
          "they opened with.", {"choice": str})
    async def set_next_agent_model(args):
        choice = resolve_model(str(args["choice"]))
        if choice is None:
            return _say(f"That named no model or effort I know. Still on {desk.running_on()}.")
        return _say(f"Next agent goes on {desk.choose(*choice)}.")

    @tool("file_improvement", "File one self-improvement item on the user's Enhancements list, "
          "the moment they ask for it. One call per item.", {"item": str})
    async def file_improvement(args):
        file_enhancement(str(args["item"]))
        return _say("Filed.")

    @tool("close_agent_tab", "Wrap up a finished agent: its tab closes (the log is archived), "
          "its session ends, and its worktree is removed. Call it unprompted once the user has "
          "signed off on that agent's work and it has landed - they never want to see a "
          "finished agent lingering. Only for agents that are done - a working agent's tab "
          "stays open.", {"name": str})
    async def close_agent_tab(args):
        name = str(args["name"]).strip()
        if not desk.retire(name):
            return _say(f"Couldn't close {name} - it is still working, or there is no tab by "
                        "that name.")
        return _say(f"Closed {name}'s tab.")

    @tool("mark_ready", "Record that an agent's finished work is standing up for the user to SEE, "
          "with the exact click-by-click steps from the agent's report. Call it the moment an "
          "agent presents reviewable work; a verdict can only be recorded on work marked ready.",
          {"name": str, "steps": str})
    async def mark_ready(args):
        name = str(args["name"]).strip()
        try:
            desk.present(name, str(args["steps"]))
        except DeliveryError as refused:
            return _say(str(refused))
        return _say(f"Marked: {name}'s work is presented, awaiting their verdict.")

    @tool("record_verdict", "Record the user's verdict on presented work, the moment they give "
          "it. `verdict` is 'approved' or 'rejected'. Approval sends the agent to land the work; "
          "rejection sends it back with `feedback` - their words on what was wrong.",
          {"name": str, "verdict": str, "feedback": str})
    async def record_verdict(args):
        name = str(args["name"]).strip()
        word = str(args["verdict"]).strip().lower()
        if word not in ("approved", "rejected"):
            return _say("Say a verdict of exactly approved or rejected.")
        try:
            desk.verdict(name, word == "approved", feedback=str(args.get("feedback") or ""))
        except DeliveryError as refused:
            return _say(str(refused))
        if word == "approved":
            return _say(f"Recorded. {name} is off to land it and will report when it's in.")
        return _say(f"Recorded. {name} has their feedback and will present again.")

    tools = [start_agent, tell_agent, set_next_agent_model, file_improvement, close_agent_tab,
             mark_ready, record_verdict]
    return create_sdk_mcp_server(name=SERVER, tools=tools), tools


def _say(text):
    return {"content": [{"type": "text", "text": text}]}
