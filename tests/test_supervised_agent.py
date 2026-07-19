import asyncio

from entity.supervised_agent import SupervisedAgent, _agent_options, _permission_handler


class FakeSession:
    def __init__(self, options):
        self.options = options
        self.asked = []

    def ask(self, message, on_step=None):
        self.asked.append(message)
        return f"report: did {message}"

    def close(self):
        self.closed = True


def test_agent_runs_approval_gated_and_isolated_from_the_global_config():
    opts = _agent_options("C:/work/tree", "sonnet", can_use_tool=lambda *a: None)

    assert opts.cwd == "C:/work/tree"
    # nothing runs without a decision: approvals stay ON and every request routes to can_use_tool
    assert opts.permission_mode == "default"
    assert opts.can_use_tool is not None
    # load NO settings — the worktrees sit under his home, so any discovery would drag in his
    # global companion-format CLAUDE.md + Stop hook and contaminate the agent.
    assert list(opts.setting_sources) == []


def test_permission_handler_allows_when_the-user_approves():
    seen = {}

    async def decide(name, tool, tool_input):
        seen["args"] = (name, tool, tool_input)
        return True

    handler = _permission_handler("the-tracker-note", decide)
    result = asyncio.run(handler("Bash", {"command": "npm test"}, None))

    assert result.behavior == "allow"
    assert seen["args"] == ("the-tracker-note", "Bash", {"command": "npm test"})


def test_permission_handler_denies_when_the-user_declines():
    async def decide(name, tool, tool_input):
        return False

    handler = _permission_handler("the-tracker-note", decide)
    result = asyncio.run(handler("Bash", {"command": "rm -rf"}, None))

    assert result.behavior == "deny"


def test_work_sends_the_message_and_returns_the_agents_report():
    async def decide(*a):
        return True

    agent = SupervisedAgent("the-tracker-note-destination", "C:/wt", decide, session_factory=FakeSession)

    report = agent.work("continue your task")

    assert report == "report: did continue your task"
    assert agent.name == "the-tracker-note-destination"
