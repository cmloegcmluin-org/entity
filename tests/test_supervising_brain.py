from pathlib import Path

from entity.supervising_brain import SupervisingBrain, _resolve, parse_supervise


class FakeInner:
    def __init__(self, reply):
        self._reply = reply
        self.heard = []

    def respond(self, utterance):
        self.heard.append(utterance)
        return self._reply


def test_parse_supervise_extracts_the_target():
    assert parse_supervise("[SUPERVISE] ~/workspace/notecraft/.claude/worktrees") == "~/workspace/notecraft/.claude/worktrees"
    assert parse_supervise("Sure. [SUPERVISE] /a, /b\nignored") == "/a, /b"
    assert parse_supervise("Just chatting, no directive here.") is None


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
    brain = SupervisingBrain(inner, io=None)

    brain.interrupt()

    assert inner.interrupted is True  # a barge-in reaches the real brain through the wrapper


def test_normal_replies_pass_straight_through():
    inner = FakeInner("just a normal spoken reply")

    brain = SupervisingBrain(inner, io=None, supervise_fn=None, resolve=None)

    assert brain.respond("hi") == "just a normal spoken reply"


def test_a_supervise_directive_runs_the_fleet_and_reports_back():
    inner = FakeInner("[SUPERVISE] /work/trees")
    called = {}

    def fake_supervise(paths, io, model, log):
        called["paths"] = paths
        called["io"] = io
        return {"a": "report", "b": "report"}

    brain = SupervisingBrain(
        inner, io="THE_IO", supervise_fn=fake_supervise, resolve=lambda t: ["/work/trees/a", "/work/trees/b"]
    )

    said = brain.respond("resume my sessions")

    assert called["paths"] == ["/work/trees/a", "/work/trees/b"]
    assert called["io"] == "THE_IO"
    assert "2" in said  # tells the user it supervised two


def test_a_fresh_transcript_log_is_opened_for_the_session_and_handed_to_the_fleet():
    inner = FakeInner("[SUPERVISE] /work/trees")
    called = {}

    def fake_supervise(paths, io, model, log):
        called["log"] = log
        return {"a": "report"}

    brain = SupervisingBrain(
        inner,
        io="THE_IO",
        supervise_fn=fake_supervise,
        resolve=lambda t: ["/work/trees/a"],
        make_log=lambda target: f"LOG_FOR:{target}",
    )

    brain.respond("resume my sessions")

    assert called["log"] == "LOG_FOR:/work/trees"  # the session's log, keyed to what he named
