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


def test_a_supervise_directive_starts_an_agent_per_worktree():
    desk = FakeDesk()
    brain = _brain(
        FakeInner("[SUPERVISE] /work/trees"), desk,
        resolve=lambda target: ["/work/trees/a", "/work/trees/b"],
    )

    said = brain.respond("resume my sessions")

    assert [name for name, _, _ in desk.started] == ["a", "b"]
    assert [cwd for _, cwd, _ in desk.started] == ["/work/trees/a", "/work/trees/b"]
    assert said == ""  # the ack covered it; the agents' tabs appear on their own


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
    assert said == ""  # and control came straight back, with no report to wait for


def test_a_worktree_that_does_not_exist_yet_is_cut_fresh_first():
    prepared = []
    desk = FakeDesk()
    brain = _brain(
        FakeInner("[SUPERVISE] /work/trees"), desk,
        resolve=lambda target: ["/definitely/not/here"], prepare=prepared.append,
    )

    brain.respond("start something new")

    assert prepared == ["/definitely/not/here"]  # new work means a new worktree, cut before the agent


def test_an_improve_directive_files_the_enhancement():
    filed = []
    brain = _brain(FakeInner("[IMPROVE] level meter should show clipping"), FakeDesk(), file_enhancement=filed.append)

    said = brain.respond("file a self-improvement: the level meter should show clipping")

    assert filed == ["level meter should show clipping"]
    assert said == ""  # filed; the tab shows it landing, and the ack said it was heard


def test_a_tell_directive_reaches_an_agent_already_running():
    desk = FakeDesk(knows={"fixer"})
    brain = _brain(FakeInner("[TELL] fixer: folder level, not file level"), desk)

    said = brain.respond("tell it I only need the folder")

    assert desk.sent == [("fixer", "folder level, not file level")]
    assert said == ""  # delivered; only a failure to deliver would speak


def test_what_the_brain_said_to_him_survives_a_directive():
    # His all-caps demand for a way to actually test the work was answered, in full, with "Passed
    # that to drive-native-gdoc-export." The wrapper threw away every word the brain had for him and
    # substituted a canned line. Eight turns in one session went out that way - so any turn where he
    # asked something AND something got filed or dispatched, his question simply went unanswered.
    desk = FakeDesk(knows={"fixer"})
    brain = _brain(FakeInner("Yes - you need to see it running before you sign anything off.\n"
                             "[TELL] fixer: stand a test instance up on another port"), desk)

    said = brain.respond("I NEED TO SEE THE NEW WORK IN ACTION")

    assert desk.sent == [("fixer", "stand a test instance up on another port")]
    assert said == "Yes - you need to see it running before you sign anything off."


def test_a_filing_can_carry_an_answer_before_or_after_it():
    # "Filed." was the whole reply to "dig into the log files for last session", and he asked back
    # "Filed? What do you mean filed?" - the filing had eaten the answer.
    filed = []
    brain = _brain(FakeInner("[IMPROVE] level meter should show clipping\n"
                             "Noted - I'll have that in the tab in a moment."),
                   FakeDesk(), file_enhancement=filed.append)

    said = brain.respond("file that, and tell me when it's there")

    assert filed == ["level meter should show clipping"]
    assert said == "Noted - I'll have that in the tab in a moment."


def test_every_improvement_asked_for_is_filed_not_just_the_first():
    # "Well, you filed one of the two. Please file the other one." Only the first marker line was
    # ever read, so asking for two tickets filed one and cost him another round to notice and say so.
    filed = []
    brain = _brain(FakeInner("On it.\n"
                             "[IMPROVE] stop claiming a longer answer when the answer is one word\n"
                             "[IMPROVE] be aware of everything said in your own name"),
                   FakeDesk(), file_enhancement=filed.append)

    said = brain.respond("file both of those")

    assert filed == ["stop claiming a longer answer when the answer is one word",
                     "be aware of everything said in your own name"]
    assert said == "On it."  # and no marker line is left in what he hears


def test_a_directive_with_nothing_said_alongside_it_is_covered_by_the_ack():
    # "Can you see how it's a waste of my time... this could all be collapsed into a single 'Got
    # it.' within 5 seconds." The ack he hears at the top of every turn already confirms receipt,
    # so a success with nothing else to say says nothing more - one "Got it." total, as he asked.
    # Only a SUCCESS may be silent: failures below still speak.
    desk = FakeDesk(knows={"fixer"})

    assert _brain(FakeInner("[TELL] fixer: go"), desk).respond("tell it") == ""
    assert _brain(FakeInner("[IMPROVE] a thing"), FakeDesk(),
                  file_enhancement=lambda item: None).respond("file it") == ""
    assert _brain(FakeInner("[SUPERVISE] /work/trees"), FakeDesk(),
                  resolve=lambda target: ["/work/trees/a"]).respond("go") == ""


def test_a_malformed_marker_is_never_read_out_as_code():
    # "I don't appreciate how you're speaking to me in code. We're supposed to be having a
    # conversation as human like Entities." A marker it fumbled - no colon after the agent, an empty
    # target, a blank item - parsed as nothing, fell past every branch, and the raw reply went out
    # bracket and all. Whatever else happens, marker syntax is not something he hears.
    desk = FakeDesk(knows={"fixer"})

    for fumbled in ("[TELL] fixer no colon here", "[SUPERVISE]\nsome task", "[IMPROVE]"):
        said = _brain(FakeInner(fumbled), desk, file_enhancement=lambda item: None).respond("go")

        assert "[" not in said and "]" not in said, fumbled


def test_a_fumbled_marker_admits_it_rather_than_going_quiet():
    # Silently swallowing it would be worse than the code: he'd be left believing the thing was
    # filed or sent, and find out much later that nothing had happened at all.
    brain = _brain(FakeInner("[TELL] fixer no colon here"), FakeDesk(knows={"fixer"}))

    said = brain.respond("tell it to stop")

    assert said and "didn't" in said.lower()


def test_a_tell_to_an_agent_that_is_not_running_says_so_rather_than_pretending():
    desk = FakeDesk()
    brain = _brain(FakeInner("[TELL] ghost: are you there"), desk)

    said = brain.respond("ask it")

    assert desk.sent == []
    assert "don't have an agent" in said
