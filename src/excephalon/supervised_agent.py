"""A supervised coding agent working in one git worktree.

Approvals stay ON (`permission_mode="default"`): every time the agent wants to act, its
`can_use_tool` fires and asks the user (through the injected async `decide`) before anything
runs - this is an approval-gated relay, NOT an unattended agent. A persistent session lets the
agent remember its task across turns; `work(message)` sends it a task (or the user's answer)
and returns what it says back, which the fleet layer relays onward.
"""

from claude_agent_sdk import ClaudeAgentOptions, PermissionResultAllow, PermissionResultDeny

from excephalon.models import DEFAULT_EFFORT, DEFAULT_MODEL
from excephalon.sdk_session import SdkSession


def _permission_handler(name, decide):
    """Turn an agent's tool request into an allow/deny, and the decision into a result.

    `decide` returns True to allow, or a reason string to deny (a mechanical refusal the desk makes
    itself - see `agent_desk.landing_block_reason`). The string must be tested with `is True`,
    never for truthiness: a reason string is truthy, and mistaking it for approval would let
    through the very landing it was refusing."""

    async def can_use_tool(tool_name, tool_input, context):
        decision = await decide(name, tool_name, tool_input)
        if decision is True:
            return PermissionResultAllow(behavior="allow", updated_input=tool_input)
        message = decision if isinstance(decision, str) else "the user said not now"
        return PermissionResultDeny(behavior="deny", message=message, interrupt=False)

    return can_use_tool


def _agent_options(cwd, model, effort, can_use_tool, resume=None):
    return ClaudeAgentOptions(
        cwd=cwd,
        model=model,
        effort=effort,  # how hard it is told to think; their choice, said out loud (see excephalon.models)
        # Only the PROJECT's settings: the worktree's checked-in CLAUDE.md - TDD, the merge
        # process, everything a repo demands of anyone working in it - loads into every agent.
        # The USER scope stays out, deliberately and verified: his global CLAUDE.md carries
        # reply-format rules and a Stop hook written for agents he talks to directly, and loaded
        # into a working agent they had it answering in his quoting format with its latency in
        # ruins. "how to TDD etc. is ultra critical" - that is project law; the quoting rules are
        # his-conversation law; the split keeps each where it belongs.
        setting_sources=["project"],
        # Pinned against account-level claude.ai connectors, which attach to any session and
        # wedge a headless one on a browserless OAuth (anthropics/claude-code#36060). This also
        # keeps out any .mcp.json a repo might carry - none of his do today, and a wedged agent
        # mid-landing costs more than a hypothetical project server.
        extra_args={"strict-mcp-config": None},
        permission_mode="default",  # approvals ON: nothing runs without a decision
        can_use_tool=can_use_tool,
        # Reattach to everything the agent already knew: a restart used to strand the fleet, and
        # the session id is what makes an agent outlive the process that started it.
        resume=resume,
    )


class SupervisedAgent:
    def __init__(self, name, cwd, decide, *, model=DEFAULT_MODEL, effort=DEFAULT_EFFORT,
                 session_factory=SdkSession, resume=None):
        self.name = name
        self._session = session_factory(
            _agent_options(cwd, model, effort, _permission_handler(name, decide), resume))

    @property
    def session_id(self):
        """The CLI session this agent lives in - what a future process resumes it by."""
        return getattr(self._session, "last_session_id", None)

    def work(self, message, on_message=None):
        """Do a piece of work, handing over everything it streams back as it happens - what it
        said and what it ran alike, since the desk is what decides how to record it."""
        return self._session.ask(message, on_message=on_message)

    def close(self):
        self._session.close()
