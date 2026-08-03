import asyncio

from excephalon.supervised_agent import SupervisedAgent, _agent_options, _permission_handler


class FakeSession:
    def __init__(self, options):
        self.options = options
        self.asked = []

    def ask(self, message, on_message=None):
        self.asked.append(message)
        if on_message is not None:
            on_message(f"a message about {message}")
        return f"report: did {message}"

    def close(self):
        self.closed = True


def test_agent_reads_the_repos_own_rules_but_never_the_users_global_config():
    opts = _agent_options("C:/work/tree", "claude-opus-4-8", "high", can_use_tool=lambda *a: None)

    assert opts.cwd == "C:/work/tree"
    # nothing runs without a decision: approvals stay ON and every request routes to can_use_tool
    assert opts.permission_mode == "default"
    assert opts.can_use_tool is not None
    # The split he asked for: the PROJECT's checked-in CLAUDE.md (TDD, merge process, repo law)
    # reaches every agent, while the USER scope - his reply-format rules and Stop hooks, written
    # for agents he talks to directly - never does: loaded once by accident, agents answered in
    # his quoting format and their latency fell apart.
    assert list(opts.setting_sources) == ["project"]


def test_permission_handler_allows_when_the_user_approves():
    seen = {}

    async def decide(name, tool, tool_input):
        seen["args"] = (name, tool, tool_input)
        return True

    handler = _permission_handler("docs-sidebar", decide)
    result = asyncio.run(handler("Bash", {"command": "npm test"}, None))

    assert result.behavior == "allow"
    assert seen["args"] == ("docs-sidebar", "Bash", {"command": "npm test"})


def test_permission_handler_denies_when_the_user_declines():
    async def decide(name, tool, tool_input):
        return False

    handler = _permission_handler("docs-sidebar", decide)
    result = asyncio.run(handler("Bash", {"command": "rm -rf"}, None))

    assert result.behavior == "deny"


def test_permission_handler_denies_with_the_reason_the_desk_gives():
    # The desk returns a REASON string (not just False) when it refuses an unapproved landing; the
    # handler must deny AND pass that reason to the agent, so a blocked push teaches the flow rather
    # than reading as a bare "no". A truthy string must never be mistaken for approval.
    async def decide(name, tool, tool_input):
        return "Landing is blocked: the user has not approved this work yet."

    handler = _permission_handler("fixer", decide)
    result = asyncio.run(handler("Bash", {"command": "gh pr merge --auto"}, None))

    assert result.behavior == "deny"
    assert result.message == "Landing is blocked: the user has not approved this work yet."


def test_work_sends_the_message_and_returns_the_agents_report():
    async def decide(*a):
        return True

    agent = SupervisedAgent("docs-sidebar", "C:/wt", decide, session_factory=FakeSession)

    report = agent.work("continue your task")

    assert report == "report: did continue your task"
    assert agent.name == "docs-sidebar"


def test_work_hands_the_desk_every_message_the_session_streams():
    # The desk is what writes the log, so a watcher dropped here is a log with nothing in it.
    async def decide(*a):
        return True

    agent = SupervisedAgent("docs-sidebar", "C:/wt", decide, session_factory=FakeSession)
    watching = []

    agent.work("continue your task", on_message=watching.append)

    assert watching == ["a message about continue your task"]


def test_an_agent_can_be_reopened_on_its_old_session():
    # A restart used to strand the fleet; the resume id reattaches an agent to everything it knew.
    made = []

    def factory(options):
        made.append(options)
        return FakeSession(options)

    async def decide(*a):
        return True

    agent = SupervisedAgent("fixer", "C:/wt", decide, session_factory=factory,
                            resume="sess-42")

    assert made[0].resume == "sess-42"


def test_the_agents_session_id_is_readable_for_the_record():
    async def decide(*a):
        return True

    class RememberingSession(FakeSession):
        last_session_id = "sess-fixer-7"

    agent = SupervisedAgent("fixer", "C:/wt", decide,
                            session_factory=lambda options: RememberingSession(options))

    assert agent.session_id == "sess-fixer-7"
