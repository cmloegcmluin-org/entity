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
        self.presented = []
        self.verdicts = []
        self._known = set(known)

    def start(self, name, cwd, task, enhancement=None):
        self.started.append((name, cwd, task, enhancement))

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

    def present(self, name, steps):
        from entity.delivery import DeliveryError

        if name not in self._known:
            raise DeliveryError(f"no agent called {name} is at the desk")
        self.presented.append((name, steps))

    def verdict(self, name, approved, feedback=""):
        from entity.delivery import DeliveryError

        if name not in self._known:
            raise DeliveryError("no verdict can be recorded - nothing has been presented")
        self.verdicts.append((name, approved, feedback))


def _call(tool, **args):
    reply = asyncio.run(tool.handler(args))
    [content] = reply["content"]
    return content["text"]


class FakeForeman:
    def __init__(self):
        self.considered = []

    def consider(self, name, question):
        self.considered.append((name, question))


def _tools(desk, foreman=None, **kwargs):
    server, tools = fleet_actions(desk, foreman or FakeForeman(), **kwargs)
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

    assert desk.started == [("fix-drive-link", str(worktree), "fix the drive link", None)]
    assert "fix-drive-link" in said


def test_start_agent_tags_the_agent_with_the_enhancement_it_takes_on(tmp_path):
    # When the agent is taking an item off the Enhancements list, that item rides along verbatim so
    # it ticks itself off the list when the work lands (agent_desk.retire).
    desk = FakeDesk()
    worktree = tmp_path / "better-voice"
    worktree.mkdir()
    tools = _tools(desk, resolve=lambda target: [str(worktree)], prepare=lambda path: None)

    _call(tools["start_agent"], path=str(worktree), task="wire the neural voice",
          enhancement="Better voice")

    assert desk.started == [("better-voice", str(worktree), "wire the neural voice", "Better voice")]


def test_start_agent_leaves_the_tag_empty_when_no_enhancement_is_named(tmp_path):
    # Most work is not a listed enhancement; a blank tag must become no tag, never an empty-string
    # item the wrap-up then tries to tick off nothing with.
    desk = FakeDesk()
    worktree = tmp_path / "one-off"
    worktree.mkdir()
    tools = _tools(desk, resolve=lambda target: [str(worktree)], prepare=lambda path: None)

    _call(tools["start_agent"], path=str(worktree), task="a one-off fix", enhancement="  ")

    assert desk.started == [("one-off", str(worktree), "a one-off fix", None)]


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


def test_updating_the_persona_records_a_standing_instruction():
    # The gap this closes: Entity could file an enhancement but had no lever to change how it
    # itself behaves. A typed tool, like every other - it cannot be half-written or leak into the
    # voice, and it lands in the same overlay the window edits.
    desk = FakeDesk()
    added = []
    tools = _tools(desk, add_persona=added.append)

    said = _call(tools["update_persona"], instruction="never read a commit hash aloud")

    assert added == ["never read a commit hash aloud"]
    assert "persona" in said.lower() or "standing" in said.lower()


def test_remembering_a_fact_appends_it_to_what_entity_has_learned():
    # Write access to its memory: told a durable fact, Entity can keep it now, not only at the
    # end-of-session consolidation. Facts arrive as a list, the way `append_learned` takes them.
    desk = FakeDesk()
    remembered = []
    tools = _tools(desk, remember_fact=lambda facts: remembered.extend(facts))

    said = _call(tools["remember"], fact="they keep their coffee mug on the left")

    assert remembered == ["they keep their coffee mug on the left"]
    assert "remember" in said.lower() or "noted" in said.lower()


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


def test_mark_ready_records_the_presentation_with_its_steps():
    desk = FakeDesk()
    tools = _tools(desk)

    said = _call(tools["mark_ready"], name="gdoc-export",
                 steps="Open localhost:5300 and click Export.")

    assert desk.presented == [("gdoc-export", "Open localhost:5300 and click Export.")]
    assert "gdoc-export" in said


def test_mark_ready_relays_the_desks_refusal():
    # The refusal sentence IS the tool's answer - the model must hear why, or it will tell the
    # user something was presented that wasn't.
    desk = FakeDesk(known=())
    tools = _tools(desk)

    said = _call(tools["mark_ready"], name="ghost", steps="steps")

    assert desk.presented == []
    assert "no agent" in said.lower()


def test_an_approving_verdict_reaches_the_desk():
    desk = FakeDesk()
    tools = _tools(desk)

    said = _call(tools["record_verdict"], name="gdoc-export", verdict="approved", feedback="")

    assert desk.verdicts == [("gdoc-export", True, "")]
    assert "land" in said.lower()


def test_a_rejecting_verdict_carries_the_feedback():
    desk = FakeDesk()
    tools = _tools(desk)

    said = _call(tools["record_verdict"], name="gdoc-export", verdict="rejected",
                 feedback="The button is on the wrong side.")

    assert desk.verdicts == [("gdoc-export", False, "The button is on the wrong side.")]
    assert "feedback" in said.lower()


def test_a_verdict_word_that_is_neither_is_refused_without_touching_the_desk():
    desk = FakeDesk()
    tools = _tools(desk)

    said = _call(tools["record_verdict"], name="gdoc-export", verdict="maybe", feedback="")

    assert desk.verdicts == []
    assert "approved" in said and "rejected" in said  # the two words it must choose between


def test_record_verdict_relays_the_desks_refusal():
    desk = FakeDesk(known=())
    tools = _tools(desk)

    said = _call(tools["record_verdict"], name="ghost", verdict="approved", feedback="")

    assert desk.verdicts == []
    assert "presented" in said.lower()


def test_ask_foreman_hands_the_stuck_agent_to_the_senior_layer():
    desk, foreman = FakeDesk(), FakeForeman()
    tools = _tools(desk, foreman=foreman)

    said = _call(tools["ask_foreman"], name="gdoc-export",
                 question="It wants to know which auth library to use.")

    assert foreman.considered == [("gdoc-export", "It wants to know which auth library to use.")]
    assert "foreman" in said.lower()
