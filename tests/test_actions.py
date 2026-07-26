import asyncio
import os.path
from pathlib import Path

from entity.actions import _resolve, fleet_actions


class FakeDesk:
    def __init__(self, known=("gdoc-export",)):
        self.started = []
        self.told = []
        self.chosen = []
        self.retired = []
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

    def retire(self, name):
        self.retired.append(name)
        return name in self._known


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


def test_start_agent_with_an_empty_task_falls_back_to_the_default():
    desk = FakeDesk()
    tools = _tools(desk, resolve=lambda target: ["/wt/resume-me"], prepare=lambda path: None,
                   default_task="DEFAULT TASK")

    _call(tools["start_agent"], path="/wt/resume-me", task="")

    assert desk.started[0][2] == "DEFAULT TASK"


def test_closing_a_tab_retires_the_agent_through_the_desk():
    desk = FakeDesk()
    tools = _tools(desk)

    said = _call(tools["close_agent_tab"], name="gdoc-export")

    assert desk.retired == ["gdoc-export"]
    assert "closed" in said.lower()


def test_a_tab_that_cannot_close_says_why_not_that_it_did():
    desk = FakeDesk(known=())
    tools = _tools(desk)

    said = _call(tools["close_agent_tab"], name="fixer")

    assert "still working" in said.lower() or "no tab" in said.lower()


def test_resolve_globs_a_worktrees_container_to_its_actual_worktrees(tmp_path):
    for name in ("wt1", "wt2"):
        (tmp_path / name).mkdir()
        (tmp_path / name / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
    (tmp_path / "junk").mkdir()  # no .git - not a worktree, so no agent belongs in it

    resolved = _resolve(str(tmp_path))

    assert sorted(Path(p).name for p in resolved) == ["wt1", "wt2"]


def test_resolve_never_explodes_a_single_worktree_into_its_subdirectories(tmp_path):
    # The model named ONE worktree; globbing its subdirectories started an agent in .venv, one in
    # docs, one in src... - a whole crowd working "the task" in folders that aren't worktrees at all.
    worktree = tmp_path / "hungry-neumann"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
    for name in (".venv", "docs", "src", "tests"):
        (worktree / name).mkdir()

    assert _resolve(str(worktree)) == [str(worktree)]  # one worktree, one agent


def test_resolve_takes_explicit_comma_separated_paths():
    assert _resolve("/x/one, /x/two") == ["/x/one", "/x/two"]


def test_resolve_expands_a_home_relative_fresh_path():
    # A brand-new worktree path named for new work won't exist yet, so it falls to the
    # explicit-path branch - which must still expand ~ so the agent's cwd is real.
    assert _resolve("~/work/new-agent") == [os.path.expanduser("~/work/new-agent")]
