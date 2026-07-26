import asyncio

from entity.actions import fleet_actions


class FakeDesk:
    def __init__(self, known=("gdoc-export",)):
        self.started = []
        self.told = []
        self.chosen = []
        self._known = set(known)

    def start(self, name, cwd, task):
        self.started.append((name, cwd, task))

    def send(self, name, message):
        self.told.append((name, message))
        return name in self._known

    def choose(self, model=None, effort=None):
        self.chosen.append((model, effort))
        return "Fable on max"

    def running_on(self):
        return "Opus on high"


def _call(tool, **args):
    reply = asyncio.run(tool.handler(args))
    [content] = reply["content"]
    return content["text"]


def _tools(desk, **kwargs):
    server, tools = fleet_actions(desk, **kwargs)
    return {tool.name: tool for tool in tools}


def test_start_agent_puts_a_fresh_agent_on_the_task(tmp_path):
    # The brain used to act by writing [SUPERVISE] into its own reply for a scanner to fish out -
    # fumbled phrasing silently did nothing, and the code-words leaked into what was spoken. A
    # typed call cannot be half-written and cannot be heard.
    desk = FakeDesk()
    worktree = tmp_path / "fix-drive-link"
    worktree.mkdir()
    tools = _tools(desk, resolve=lambda target: [str(worktree)], prepare=lambda path: None)

    said = _call(tools["start_agent"], path=str(worktree), task="fix the drive link")

    assert desk.started == [("fix-drive-link", str(worktree), "fix the drive link")]
    assert "fix-drive-link" in said


def test_start_agent_makes_the_worktree_when_the_path_is_new(tmp_path):
    # New work means a new worktree cut from origin/main; the tool does the cutting so the model
    # never shells out.
    desk = FakeDesk()
    fresh = tmp_path / "new-feature"
    prepared = []
    tools = _tools(desk, resolve=lambda target: [str(fresh)],
                   prepare=lambda path: prepared.append(path))

    _call(tools["start_agent"], path=str(fresh), task="build it")

    assert prepared == [str(fresh)]


def test_start_agent_with_nowhere_to_start_says_so():
    desk = FakeDesk()
    tools = _tools(desk, resolve=lambda target: [], prepare=lambda path: None)

    said = _call(tools["start_agent"], path="~/nowhere", task="?")

    assert desk.started == []
    assert "couldn't find" in said.lower()


def test_tell_agent_reaches_the_agent_by_name():
    desk = FakeDesk()
    tools = _tools(desk)

    said = _call(tools["tell_agent"], name="gdoc-export", message="also clean up the folders")

    assert desk.told == [("gdoc-export", "also clean up the folders")]
    assert "gdoc-export" in said


def test_tell_agent_says_when_there_is_no_such_agent():
    # The model must never be told a message landed when it didn't - that is how "passed that to
    # the agent" got spoken about deliveries that never happened.
    desk = FakeDesk(known=())
    tools = _tools(desk)

    said = _call(tools["tell_agent"], name="ghost", message="hello?")

    assert "no agent" in said.lower()
    assert "ghost" in said


def test_choosing_a_model_governs_the_next_agent():
    desk = FakeDesk()
    tools = _tools(desk)

    said = _call(tools["set_next_agent_model"], choice="fable on max")

    assert desk.chosen == [("claude-fable-5", "max")]
    assert "Fable on max" in said


def test_a_choice_naming_no_model_changes_nothing():
    desk = FakeDesk()
    tools = _tools(desk)

    said = _call(tools["set_next_agent_model"], choice="the good one")

    assert desk.chosen == []
    assert "Opus on high" in said  # what they are still on, so the answer is real


def test_filing_an_improvement_lands_it_in_the_profile():
    desk = FakeDesk()
    filed = []
    tools = _tools(desk, file_enhancement=filed.append)

    said = _call(tools["file_improvement"], item="louder notification chime")

    assert filed == ["louder notification chime"]
    assert "filed" in said.lower()
