import os.path
from pathlib import Path

from entity.supervising_brain import SupervisingBrain, _resolve, parse_supervise, parse_tell


class FakeInner:
    def __init__(self, reply):
        self._reply = reply
        self.heard = []

    def respond(self, utterance):
        self.heard.append(utterance)
        return self._reply


class FakeDesk:
    def __init__(self, *, knows=()):
        self.started = []
        self.sent = []
        self._knows = set(knows)

    def start(self, name, cwd, task):
        self.started.append((name, cwd, task))
        self._knows.add(name)

    def send(self, name, message):
        if name not in self._knows:
            return False
        self.sent.append((name, message))
        return True


def _brain(inner, desk, **kwargs):
    kwargs.setdefault("task", "THE TASK")
    kwargs.setdefault("prepare", lambda path: path)
    return SupervisingBrain(inner, desk, **kwargs)


def test_parse_supervise_extracts_the_target_and_the_task():
    assert parse_supervise("[SUPERVISE] ~/wts\nFinish the WIP commits.\nTests green.") == (
        "~/wts", "Finish the WIP commits.\nTests green.")
    assert parse_supervise("Sure. [SUPERVISE] /a, /b") == ("/a, /b", None)  # no task lines -> default
    assert parse_supervise("Just chatting, no directive here.") is None


def test_parse_tell_extracts_the_agent_and_the_whole_message():
    assert parse_tell("[TELL] fixer: only the subfolder, not the file") == ("fixer", "only the subfolder, not the file")
    # A correction can run several lines - all of it is the message, none of it is dropped.
    assert parse_tell("[TELL] fixer: first line\nand the rest of it") == ("fixer", "first line\nand the rest of it")
    assert parse_tell("[TELL] no colon here") is None
    assert parse_tell("nothing to see") is None


def test_resolve_globs_a_worktrees_container_to_its_actual_worktrees(tmp_path):
    for name in ("wt1", "wt2"):
        (tmp_path / name).mkdir()
        (tmp_path / name / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
    (tmp_path / "junk").mkdir()  # no .git - not a worktree, so no agent belongs in it

    resolved = _resolve(str(tmp_path))

    assert sorted(Path(p).name for p in resolved) == ["wt1", "wt2"]


def test_resolve_never_explodes_a_single_worktree_into_its_subdirectories(tmp_path):
    # The brain named ONE worktree; globbing its subdirectories started an agent in .venv, one in
    # docs, one in src... - a whole crowd working "the task" in folders that aren't worktrees at all.
    worktree = tmp_path / "hungry-neumann"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
    for name in (".venv", "docs", "src", "tests"):
        (worktree / name).mkdir()

    assert _resolve(str(worktree)) == [str(worktree)]  # one worktree, one agent


def test_resolve_takes_explicit_comma_separated_paths():
    assert _resolve("/x/one, /x/two") == ["/x/one", "/x/two"]


def test_interrupt_is_forwarded_to_the_inner_brain():
    class Interruptible(FakeInner):
        def __init__(self):
            super().__init__("reply")
            self.interrupted = False

        def interrupt(self):
            self.interrupted = True

    inner = Interruptible()
    brain = _brain(inner, FakeDesk())

    brain.interrupt()

    assert inner.interrupted is True  # a barge-in reaches the real brain through the wrapper


def test_resolve_expands_a_home_relative_fresh_path():
    # A brand-new worktree path the brain names for new work won't exist yet, so it falls to the
    # explicit-path branch - which must still expand ~ so the agent's cwd is real.
    assert _resolve("~/work/new-agent") == [os.path.expanduser("~/work/new-agent")]


def test_normal_replies_pass_straight_through():
    brain = _brain(FakeInner("just a normal spoken reply"), FakeDesk())

    assert brain.respond("hi") == "just a normal spoken reply"


def test_a_supervise_directive_starts_an_agent_per_worktree_and_says_so():
    desk = FakeDesk()
    brain = _brain(
        FakeInner("[SUPERVISE] /work/trees"), desk,
        resolve=lambda target: ["/work/trees/a", "/work/trees/b"],
    )

    said = brain.respond("resume my sessions")

    assert [name for name, _, _ in desk.started] == ["a", "b"]
    assert [cwd for _, cwd, _ in desk.started] == ["/work/trees/a", "/work/trees/b"]
    assert "2" in said  # tells the user it started two


def test_the_task_the_user_gave_travels_with_the_directive_to_the_agent():
    # Without this the brain had no way to pass their requirements on, so it went and worked the
    # request out itself - 45 seconds of digging before they heard a word, on a pure relay.
    desk = FakeDesk()
    brain = _brain(
        FakeInner("[SUPERVISE] /work/trees\nFinish the six WIP commits, get the tests green,\n"
                  "and don't merge until the user has verified it themselves."),
        desk, resolve=lambda target: ["/work/trees/a"],
    )

    brain.respond("pick up the drive subfolder work")

    _, _, task = desk.started[0]
    assert "six WIP commits" in task and "verified it themselves" in task  # their ask, not a canned task


def test_a_directive_with_no_task_lines_falls_back_to_the_default_task():
    desk = FakeDesk()
    brain = _brain(FakeInner("[SUPERVISE] /work/trees"), desk,
                   resolve=lambda target: ["/work/trees/a"], task="DEFAULT TASK")

    brain.respond("resume it")

    assert desk.started[0][2] == "DEFAULT TASK"


def test_starting_agents_does_not_wait_for_any_of_them():
    # The whole point: the reply comes back now, not when the agents are finished.
    desk = FakeDesk()
    brain = _brain(FakeInner("[SUPERVISE] /work/trees"), desk, resolve=lambda target: ["/work/trees/a"])

    said = brain.respond("go")

    assert desk.started  # it was dispatched
    assert "Started" in said  # and answered immediately, with no report to wait for


def test_a_worktree_that_does_not_exist_yet_is_cut_fresh_first():
    prepared = []
    desk = FakeDesk()
    brain = _brain(
        FakeInner("[SUPERVISE] /work/trees"), desk,
        resolve=lambda target: ["/definitely/not/here"], prepare=prepared.append,
    )

    brain.respond("start something new")

    assert prepared == ["/definitely/not/here"]  # new work means a new worktree, cut before the agent


def test_an_improve_directive_files_the_enhancement_and_says_so():
    filed = []
    brain = _brain(FakeInner("[IMPROVE] level meter should show clipping"), FakeDesk(), file_enhancement=filed.append)

    said = brain.respond("file a self-improvement: the level meter should show clipping")

    assert filed == ["level meter should show clipping"]
    assert "Filed" in said


def test_a_tell_directive_reaches_an_agent_already_running():
    desk = FakeDesk(knows={"fixer"})
    brain = _brain(FakeInner("[TELL] fixer: folder level, not file level"), desk)

    said = brain.respond("tell it I only need the folder")

    assert desk.sent == [("fixer", "folder level, not file level")]
    assert "fixer" in said


def test_a_tell_to_an_agent_that_is_not_running_says_so_rather_than_pretending():
    desk = FakeDesk()
    brain = _brain(FakeInner("[TELL] ghost: are you there"), desk)

    said = brain.respond("ask it")

    assert desk.sent == []
    assert "don't have an agent" in said
