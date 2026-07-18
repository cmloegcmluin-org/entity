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


def test_parse_supervise_extracts_the_target():
    assert parse_supervise("[SUPERVISE] ~/workspace/notecraft/.claude/worktrees") == "~/workspace/notecraft/.claude/worktrees"
    assert parse_supervise("Sure. [SUPERVISE] /a, /b\nignored") == "/a, /b"
    assert parse_supervise("Just chatting, no directive here.") is None


def test_parse_tell_extracts_the_agent_and_the_message():
    assert parse_tell("[TELL] fixer: only the subfolder, not the file") == ("fixer", "only the subfolder, not the file")
    assert parse_tell("[TELL] fixer:   spaces trimmed  \nignored") == ("fixer", "spaces trimmed")
    assert parse_tell("[TELL] no colon here") is None
    assert parse_tell("nothing to see") is None


def test_resolve_globs_a_worktrees_directory(tmp_path):
    (tmp_path / "wt1").mkdir()
    (tmp_path / "wt2").mkdir()

    resolved = _resolve(str(tmp_path))

    assert sorted(Path(p).name for p in resolved) == ["wt1", "wt2"]


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
