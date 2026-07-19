"""A supervised coding agent working in one git worktree.

Approvals stay ON (`permission_mode="default"`): every time the agent wants to act, its
`can_use_tool` fires and asks the user (through the injected async `decide`) before anything
runs - this is the approval-gated relay he asked for, NOT an unattended agent. A persistent
session lets the agent remember its task across turns; `work(message)` sends it a task (or
the user's answer) and returns what it says back, which the fleet layer relays to the user.
"""

from claude_agent_sdk import ClaudeAgentOptions, PermissionResultAllow, PermissionResultDeny

from entity.sdk_session import SdkSession


def _permission_handler(name, decide):
    """Turn an agent's tool request into a yes/no for the user, and his answer into a result."""

    async def can_use_tool(tool_name, tool_input, context):
        if await decide(name, tool_name, tool_input):
            return PermissionResultAllow(behavior="allow", updated_input=tool_input)
        return PermissionResultDeny(behavior="deny", message="the user said not now", interrupt=False)

    return can_use_tool


def _agent_options(cwd, model, can_use_tool):
    return ClaudeAgentOptions(
        cwd=cwd,
        model=model,
        # setting_sources=[] loads NO settings. Verified necessary: because the worktrees live
        # under the user's home, "project"/"local" discovery walks up into ~/.claude and drags in
        # his global companion-format CLAUDE.md + Stop hook (agent replies in ">>"/">" and latency
        # blows up). Feeding each agent its worktree's own CLAUDE.md cleanly is the next refinement.
        setting_sources=[],
        permission_mode="default",  # approvals ON: nothing runs without a decision
        can_use_tool=can_use_tool,
    )


class SupervisedAgent:
    def __init__(self, name, cwd, decide, *, model="sonnet", session_factory=SdkSession):
        self.name = name
        self._session = session_factory(_agent_options(cwd, model, _permission_handler(name, decide)))

    def work(self, message, on_step=None):
        """Do a piece of work, reporting each step as it happens (see SdkSession.ask)."""
        return self._session.ask(message, on_step=on_step)

    def close(self):
        self._session.close()
