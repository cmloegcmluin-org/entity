import threading
import time
from types import SimpleNamespace

from entity.agent_desk import AgentDesk
from entity.outbox import Outbox


def said(text):
    """One streamed message carrying the agent's own words."""
    return SimpleNamespace(content=[SimpleNamespace(text=text)])


def called(tool, **tool_input):
    return SimpleNamespace(content=[SimpleNamespace(name=tool, input=tool_input)])


def came_back(output):
    return SimpleNamespace(content=[SimpleNamespace(tool_use_id="toolu_01", content=output)])


class FakeAgent:
    """Stands in for a real SupervisedAgent: a persistent session that remembers its messages."""

    def __init__(self, name, cwd, decide, hold=None):
        self.name = name
        self.cwd = cwd
        self.decide = decide
        self.session_id = f"sess-{name}"
        self.messages = []
        self.closed = False
        self._hold = hold

    def work(self, message, on_message=None):
        self.messages.append(message)
        if on_message is not None:
            on_message(said(f"[{self.name}] did: {message}"))
        if self._hold is not None:
            self._hold.wait(2.0)
        return f"[{self.name}] did: {message}"

    def close(self):
        self.closed = True


def _desk(outbox=None, made=None, hold=None, roster=None, monitor=None, log_dir=None, run=None,
          state=None, law=None):
    outbox = outbox or Outbox()
    made = made if made is not None else []

    def factory(name, cwd, decide, **choice):
        agent = FakeAgent(name, cwd, decide, hold=hold)
        made.append(agent)
        return agent

    return (AgentDesk(outbox, agent_factory=factory, roster_path=roster, monitor=monitor,
                      log_dir=log_dir, run=run, state_path=state, law_path=law),
            outbox, made)


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class SpyMonitor:
    def __init__(self):
        self.check_ins = []
        self.finished = []

    def checked_in(self, agent):
        self.check_ins.append(agent)

    def done(self, agent):
        self.finished.append(agent)


def test_the_desk_is_what_reports_whether_an_agent_is_alive():
    # "Again, you're lying about that. I can see that the agent just checked in two minutes ago."
    # Silence was measured off the inbox FILENAMES, so a file Entity had written itself became an
    # agent that then "went quiet", and a real agent that hadn't happened to write to its inbox
    # looked dead. The desk is the only thing that knows which agents exist and when each spoke.
    monitor = SpyMonitor()
    desk, _, _ = _desk(monitor=monitor)

    desk.start("fixer", "/tmp/wt", "fix the drive link")

    assert _wait_for(lambda: monitor.finished == ["fixer"])  # the clock stops when it's done
    assert monitor.check_ins.count("fixer") >= 2  # dispatched, then again for what it narrated


def test_an_agent_that_dies_stops_its_silence_clock_too():
    # A dead agent is announced as dead; leaving its clock running would then also announce it as
    # quiet twenty minutes later, which is the same non-news twice.
    monitor = SpyMonitor()
    desk = AgentDesk(Outbox(), agent_factory=lambda *a, **k: _DyingAgent(), monitor=monitor)
    desk.start("doomed", "/tmp/wt", "try")

    assert _wait_for(lambda: monitor.finished == ["doomed"])


class _DyingAgent:
    def work(self, message, on_message=None):
        raise RuntimeError("session lost")

    def close(self):
        pass


def test_agents_start_on_the_model_he_chose_defaulting_to_opus_on_high():
    # "Sonnet's not so hot either. I usually use Opus... It should default to Opus 4.8 on High, but
    # I should be able to ask it for Fable Max for example if I want." It was hardcoded to Sonnet
    # and invisible - they asked what their agents were running and could not be told.
    started = []

    def factory(name, cwd, decide, *, model, effort):
        started.append((model, effort))
        return FakeAgent(name, cwd, decide)

    desk = AgentDesk(Outbox(), agent_factory=factory)

    desk.start("first", "/tmp/wt", "go")
    assert desk.choose("claude-fable-5", "max") == "Fable on max"  # and it says what it will be
    desk.start("second", "/tmp/wt2", "go")

    assert _wait_for(lambda: len(started) == 2)
    assert started == [("claude-opus-4-8", "high"), ("claude-fable-5", "max")]


def test_changing_the_model_leaves_an_agent_already_working_where_it_is():
    # A session's model is fixed when it opens, so a change can only govern the next agent. Saying
    # otherwise would be the kind of claim they check and find false.
    started = []
    desk = AgentDesk(Outbox(), agent_factory=lambda name, cwd, decide, *, model, effort:
                     started.append((name, model)) or FakeAgent(name, cwd, decide))

    desk.start("already-running", "/tmp/wt", "go")
    assert _wait_for(lambda: len(started) == 1)
    desk.choose("claude-fable-5", None)

    assert started == [("already-running", "claude-opus-4-8")]  # untouched by the later change
    assert desk.running_on() == "Fable on high"  # effort left alone, since they only named a model


def test_starting_an_agent_does_not_block_the_caller():
    # The conversation loop must never wait on agent work - that's what left them talking to a wall.
    hold = threading.Event()
    desk, outbox, _ = _desk(hold=hold)

    start = time.monotonic()
    desk.start("fixer", "/tmp/wt", "fix the drive link")
    elapsed = time.monotonic() - start

    assert elapsed < 0.5  # returned immediately, while the agent is still working
    assert not outbox  # and nothing is claimed to be finished yet
    hold.set()
    desk.close()


def test_the_agents_reply_arrives_in_the_outbox_when_it_lands():
    desk, outbox, _ = _desk()

    desk.start("fixer", "/tmp/wt", "fix the drive link")

    assert _wait_for(lambda: bool(outbox))
    assert any("did: fix the drive link" in message for message in outbox.drain())
    desk.close()


def test_the_news_an_agent_makes_says_which_agent_it_is_about():
    # Several ready at once are read out by name so one can be picked. The name has to travel with
    # the news: worked back out of the sentence it would be reading the label to find the thing.
    desk, outbox, _ = _desk()

    desk.start("fixer", "/tmp/wt", "fix the drive link")

    assert _wait_for(lambda: bool(outbox))
    [news] = outbox.drain()
    assert news.about == "fixer"
    desk.close()


def test_a_follow_up_reaches_the_same_agent_not_a_new_one():
    # Four agents in a row were lost because there was no live handle to talk back to.
    desk, outbox, made = _desk()
    desk.start("fixer", "/tmp/wt", "first task")
    assert _wait_for(lambda: bool(outbox))
    outbox.drain()

    assert desk.send("fixer", "now do the other half")

    assert _wait_for(lambda: bool(outbox))
    assert len(made) == 1  # the same agent, not a fresh one
    assert made[0].messages[0].startswith("first task")
    # And a follow-up is only the follow-up: the session already carries the standing rule the
    # desk attaches to a task, and repeating it every time would be most of what the tab holds.
    assert made[0].messages[1] == "now do the other half"
    desk.close()


def test_every_task_carries_the_standing_rule_to_rebase_before_showing_work():
    # "before presenting any agent branch/build to the user for verification, rebase it onto latest
    # origin/main first so nothing recently merged (e.g. other features) appears missing." The
    # brain wrote that into the dispatch on some days and not others. The desk attaches it to
    # every task, which makes it a mechanism rather than a reminder.
    desk, _, made = _desk()

    desk.start("fixer", "/tmp/wt", "fix the drive link")

    assert _wait_for(lambda: bool(made and made[0].messages))
    sent = made[0].messages[0]
    assert sent.startswith("fix the drive link")  # their ask first; the rule stands after it
    assert "rebase" in sent and "origin/main" in sent
    desk.close()


def test_a_follow_up_to_an_agent_that_was_never_started_says_so():
    desk, _, made = _desk()

    assert desk.send("ghost", "you there?") is False
    assert made == []


def test_an_agent_that_blows_up_is_reported_not_swallowed():
    outbox = Outbox()

    class Exploding:
        def work(self, message, on_message=None):
            raise RuntimeError("session died")

        def close(self):
            pass

    desk = AgentDesk(outbox, agent_factory=lambda name, cwd, decide, **choice: Exploding())

    desk.start("doomed", "/tmp/wt", "do a thing")

    assert _wait_for(lambda: bool(outbox))
    said = outbox.drain()
    assert any("doomed" in m and "session died" in m for m in said)
    assert [news.about for news in said] == ["doomed"]  # a death is news about an agent too
    desk.close()


def test_the_roster_on_disk_says_who_is_live_and_what_they_are_doing(tmp_path):
    # The Entity's own context resets kept stranding agents. The roster is a file, so it survives
    # a reset - the brain can just read it back with its ordinary tools.
    roster = tmp_path / "active-agents.txt"
    hold = threading.Event()
    desk, outbox, _ = _desk(hold=hold, roster=roster)

    desk.start("fixer", "/tmp/wt", "fix the drive link")

    assert _wait_for(lambda: roster.exists() and "fixer" in roster.read_text(encoding="utf-8"))
    written = roster.read_text(encoding="utf-8")
    assert "working" in written and "fix the drive link" in written and "/tmp/wt" in written

    hold.set()
    assert _wait_for(lambda: "idle" in roster.read_text(encoding="utf-8"))  # its state moves on
    desk.close()


def test_every_exchange_is_written_to_a_timestamped_per_agent_log(tmp_path):
    # "still no timestamps in the logs": the tailable record of what the Entity and an agent said
    # to each other, stamped, written by the desk itself as it happens - not left to the brain to
    # hand-author in whatever format it invents that day.
    outbox = Outbox()
    made = []

    def factory(name, cwd, decide, **choice):
        agent = FakeAgent(name, cwd, decide)
        made.append(agent)
        return agent

    desk = AgentDesk(outbox, agent_factory=factory, log_dir=tmp_path)
    desk.start("fixer", "/tmp/wt", "fix the drive link")
    assert _wait_for(lambda: bool(outbox))
    desk.send("fixer", "only the subfolder")
    assert _wait_for(lambda: len(outbox.drain()) >= 0 and len(made[0].messages) == 2)
    assert _wait_for(lambda: "only the subfolder" in (tmp_path / "fixer.log").read_text(encoding="utf-8"))
    desk.close()

    log = (tmp_path / "fixer.log").read_text(encoding="utf-8")
    assert "ENTITY> fix the drive link" in log
    assert "AGENT> [fixer] did: fix the drive link" in log
    assert "ENTITY> only the subfolder" in log
    for line in log.splitlines():
        if line.startswith("====="):
            continue
        assert line.startswith("["), f"unstamped line: {line!r}"  # every line carries its time


def test_closing_the_desk_shuts_its_agents_down():
    desk, outbox, made = _desk()
    desk.start("fixer", "/tmp/wt", "a task")
    assert _wait_for(lambda: bool(outbox))

    desk.close()

    assert made[0].closed


def test_an_agents_steps_reach_its_log_as_it_works(tmp_path):
    # They watched an empty log for fourteen minutes while the agent was alive and working, and
    # Entity declared it dead one minute before it answered. Being able to SEE it work is the fix.
    outbox = Outbox()
    steps = ["Reading the router.", "Writing a failing test.", "Confirmed red."]

    class NarratingAgent:
        def work(self, message, on_message=None):
            for step in steps:
                on_message(said(step))
            return "done"

        def close(self):
            pass

    desk = AgentDesk(outbox, agent_factory=lambda name, cwd, decide, **choice: NarratingAgent(),
                     log_dir=tmp_path)
    desk.start("fixer", "/tmp/wt", "do the thing")
    assert _wait_for(lambda: bool(outbox))

    log = (tmp_path / "fixer.log").read_text(encoding="utf-8")
    for step in steps:
        assert step in log  # every step, as it happened
    assert log.index("Reading the router.") < log.index("Confirmed red.")  # in order
    desk.close()


def test_what_an_agent_ran_and_what_came_back_reach_its_log(tmp_path):
    # "no tool calls, diffs, or command/test output": the log held only the sentences the agent
    # narrated, so ten minutes of real work read back as ten minutes of silence. Asked for the
    # real exchange repeatedly, because it is what says whether these agents are being driven well.
    outbox = Outbox()

    class Working:
        def work(self, message, on_message=None):
            on_message(called("Bash", command="python -m pytest -q"))
            on_message(came_back("358 passed in 4.41s"))
            on_message(said("Green. Committing."))
            return "Green. Committing."

        def close(self):
            pass

    desk = AgentDesk(outbox, agent_factory=lambda name, cwd, decide, **choice: Working(), log_dir=tmp_path)
    desk.start("fixer", "/tmp/wt", "make it green")
    assert _wait_for(lambda: bool(outbox))

    log = (tmp_path / "fixer.log").read_text(encoding="utf-8")
    assert "WORK> Bash: python -m pytest -q" in log
    assert "WORK>     358 passed in 4.41s" in log
    assert "AGENT> Green. Committing." in log
    desk.close()


def test_the_digest_briefs_a_brain_on_the_fleet_without_a_file_read():
    # "How's it going?" used to send the brain off to read the roster file with its tools - thirty
    # seconds to fifteen minutes of silence for a question about state the process already held.
    # The digest is that state as a handful of lines, handed to the brain every turn by code.
    hold = threading.Event()
    desk, outbox, _ = _desk(hold=hold)
    desk.start("fixer", "/tmp/wt", "fix the drive link so it opens the memo's own subfolder")

    briefing = desk.digest()

    assert "fixer" in briefing
    assert "working" in briefing
    assert "fix the drive link" in briefing
    hold.set()
    desk.close()


def test_the_digest_with_nothing_running_says_so():
    desk, _, _ = _desk()

    assert desk.digest() == "No agents running."


def test_with_an_events_sink_the_desk_reports_there_instead_of_the_outbox():
    # The narrator words the news in the brain's own voice; the desk's job shrinks to saying WHAT
    # happened - kind, agent, report - and staying out of the wording business entirely.
    events = []
    outbox = Outbox()
    desk = AgentDesk(outbox, agent_factory=lambda name, cwd, decide, **choice:
                     FakeAgent(name, cwd, decide), events=lambda *e: events.append(e))

    desk.start("fixer", "/tmp/wt", "fix the drive link")

    assert _wait_for(lambda: bool(events))
    kind, agent, report = events[0]
    assert (kind, agent) == ("finished", "fixer")
    assert "fix the drive link" in report
    assert not outbox  # the sink owns delivery now; nothing is pushed twice
    desk.close()


def test_a_death_reaches_the_events_sink_as_what_it_is():
    events = []
    desk = AgentDesk(Outbox(), agent_factory=lambda *a, **k: _DyingAgent(),
                     events=lambda *e: events.append(e))

    desk.start("doomed", "/tmp/wt", "try")

    assert _wait_for(lambda: bool(events))
    kind, agent, report = events[0]
    assert (kind, agent) == ("died", "doomed")
    assert "session lost" in report


def test_retiring_a_finished_agent_closes_its_tab_by_moving_its_log_to_the_archive(tmp_path):
    # A tab closes when its log leaves the folder the window watches. The brain used to do the
    # move with its own shell; it has no shell now, so the desk does it on the tool's behalf. The
    # log lands in the fleet's one archive - runtime/agent-logs-archive/, a SIBLING of the live
    # folder, named for what it is - so it is entirely outside what the roster globs.
    logs = tmp_path / "agent-logs"
    desk, outbox, _ = _desk(log_dir=logs)
    desk.start("fixer", "/tmp/wt", "a task")
    assert _wait_for(lambda: bool(outbox))  # finished: its work() returned

    assert desk.retire("fixer") is True

    assert not (logs / "fixer.log").exists()
    assert (tmp_path / "agent-logs-archive" / "fixer.log").exists()
    assert desk.roster() == []  # and the desk lets go of the finished session
    desk.close()


def test_a_working_agent_cannot_be_retired_out_from_under_them(tmp_path):
    # Closing a live agent's tab would drop the user's view into work still happening.
    hold = threading.Event()
    desk, _, _ = _desk(hold=hold, log_dir=tmp_path)
    desk.start("fixer", "/tmp/wt", "a task")
    assert _wait_for(lambda: (tmp_path / "fixer.log").exists())

    assert desk.retire("fixer") is False
    assert (tmp_path / "fixer.log").exists()

    hold.set()
    desk.close()


def test_retiring_an_agent_the_desk_never_had_still_moves_a_leftover_log(tmp_path):
    # After a restart the desk is empty but yesterday's logs still hold tabs open. Retiring one
    # is then purely the file move.
    logs = tmp_path / "agent-logs"
    logs.mkdir()
    desk, _, _ = _desk(log_dir=logs)
    (logs / "old-timer.log").write_text("x", encoding="utf-8")

    assert desk.retire("old-timer") is True
    assert (tmp_path / "agent-logs-archive" / "old-timer.log").exists()


def test_retiring_something_with_no_log_and_no_agent_says_no(tmp_path):
    desk, _, _ = _desk(log_dir=tmp_path)

    assert desk.retire("ghost") is False


def test_the_roster_says_when_each_agent_was_last_heard_from(tmp_path):
    outbox = Outbox()
    roster = tmp_path / "active-agents.txt"

    class NarratingAgent:
        def work(self, message, on_message=None):
            on_message(said("Reading the router."))
            return "done"

        def close(self):
            pass

    desk = AgentDesk(outbox, agent_factory=lambda name, cwd, decide, **choice: NarratingAgent(),
                     roster_path=roster, log_dir=tmp_path, clock=lambda fmt: "2026-07-19 08:20:15")
    desk.start("fixer", "/tmp/wt", "do the thing")
    assert _wait_for(lambda: bool(outbox))

    assert "last heard 2026-07-19 08:20:15" in roster.read_text(encoding="utf-8")
    desk.close()


def test_every_task_carries_the_standing_rule_that_review_means_their_eyes():
    # "When I say that I want to be able to verify a feature, I'm not satisfied with running a
    # test command." An agent handed back "run pytest" as the acceptance step; the rule that
    # review means a live instance and click-steps now rides with every task, like the rebase rule.
    desk, _, made = _desk()

    desk.start("fixer", "/tmp/wt", "fix the drive link")

    assert _wait_for(lambda: bool(made and made[0].messages))
    sent = made[0].messages[0]
    assert "own eyes" in sent and "live instance" in sent
    assert "Never offer 'run the tests'" in sent
    desk.close()


def test_retiring_a_finished_agent_also_removes_its_worktree(tmp_path):
    # "it should probably archive the agent log... and always do stuff like archive the Claude
    # session and worktree etc." - wrapping up is one gesture, not three chores.
    ran = []
    desk, outbox, _ = _desk(log_dir=tmp_path / "agent-logs",
                            run=lambda cmd, **kw: ran.append(cmd))
    desk.start("fixer", "/wt/fixer", "a task")
    assert _wait_for(lambda: bool(outbox))

    assert desk.retire("fixer") is True

    assert ["git", "-C", "/wt/fixer", "worktree", "remove", "/wt/fixer"] in ran
    desk.close()


def test_a_worktree_that_will_not_remove_does_not_block_the_retirement(tmp_path):
    # A dirty worktree is the sweep's business later; the tab and the session still wrap up now.
    def refuses(cmd, **kw):
        raise RuntimeError("worktree is dirty")

    logs = tmp_path / "agent-logs"
    desk, outbox, _ = _desk(log_dir=logs, run=refuses)
    desk.start("fixer", "/wt/fixer", "a task")
    assert _wait_for(lambda: bool(outbox))

    assert desk.retire("fixer") is True
    assert not (logs / "fixer.log").exists()  # the tab still closed


def test_the_desks_state_survives_on_disk_for_the_next_process(tmp_path):
    # "Obviously the agent processes must be independent of Entity. I close it and reopen it
    # constantly." The state file is the fleet's survival record: who exists, where, on which
    # CLI session - everything a fresh process needs to reattach.
    import json

    state = tmp_path / "agents.json"
    desk, outbox, _ = _desk(state=state)

    desk.start("fixer", "/wt/fixer", "fix the drive link")
    assert _wait_for(lambda: bool(outbox))

    [entry] = json.loads(state.read_text(encoding="utf-8"))
    assert entry["name"] == "fixer"
    assert entry["cwd"] == "/wt/fixer"
    assert entry["session_id"] == "sess-fixer"
    assert entry["state"] == "idle"
    desk.close()


def test_retiring_prunes_the_state_file(tmp_path):
    import json

    state = tmp_path / "agents.json"
    desk, outbox, _ = _desk(state=state, log_dir=tmp_path / "agent-logs")
    desk.start("fixer", "/wt/fixer", "a task")
    assert _wait_for(lambda: bool(outbox))

    desk.retire("fixer")

    assert json.loads(state.read_text(encoding="utf-8")) == []


def test_revive_reopens_yesterdays_agents_on_their_old_sessions(tmp_path):
    # The whole point of Milestone 3: close Entity mid-task, reopen it, and the same agents are
    # there - reattached to sessions that remember everything, with in-flight work re-kicked.
    import json

    state = tmp_path / "agents.json"
    state.write_text(json.dumps([
        {"name": "fixer", "cwd": "/wt/fixer", "task": "fix the link",
         "session_id": "sess-1", "state": "idle",
         "model": "claude-opus-4-8", "effort": "high"},
        {"name": "builder", "cwd": "/wt/builder", "task": "build the thing",
         "session_id": "sess-2", "state": "working",
         "model": "claude-fable-5", "effort": "max"},
    ]), encoding="utf-8")
    revived = []

    def factory(name, cwd, decide, *, model, effort, resume=None):
        revived.append((name, model, effort, resume))
        return FakeAgent(name, cwd, decide)

    desk = AgentDesk(Outbox(), agent_factory=factory, state_path=state)

    names = desk.revive()

    assert sorted(names) == ["builder", "fixer"]
    assert ("fixer", "claude-opus-4-8", "high", "sess-1") in revived
    assert ("builder", "claude-fable-5", "max", "sess-2") in revived
    assert "fixer" in desk.digest() and "builder" in desk.digest()
    # The one that was mid-task is told to pick back up; the idle one is not disturbed.
    assert _wait_for(lambda: any("restarted" in m for a in desk._desked.values()
                                 for m in a.agent.messages))
    fixer = desk._desked["fixer"].agent
    assert fixer.messages == []
    desk.close()


def test_revive_with_no_state_file_is_a_quiet_no_op(tmp_path):
    desk, _, _ = _desk(state=tmp_path / "missing.json")

    assert desk.revive() == []


def test_an_entry_with_no_session_id_cannot_be_revived_and_is_skipped(tmp_path):
    import json

    state = tmp_path / "agents.json"
    state.write_text(json.dumps([{"name": "ghost", "cwd": "/wt/g", "task": "?",
                                  "session_id": None, "state": "idle"}]), encoding="utf-8")
    desk, _, _ = _desk(state=state)

    assert desk.revive() == []


def _finished(desk, outbox, name="fixer", cwd="/wt/fixer", task="a task"):
    """Start one agent and wait until its first turn is done - the usual bench for delivery tests."""
    desk.start(name, cwd, task)
    assert _wait_for(lambda: bool(outbox))
    outbox.drain()
    return name


def test_presented_work_shows_in_the_digest_awaiting_a_verdict():
    desk, outbox, _ = _desk()
    _finished(desk, outbox)

    desk.present("fixer", "Open localhost:5300 and click Export.")

    assert "presented, awaiting their verdict" in desk.digest()
    desk.close()


def test_work_cannot_be_presented_while_the_agent_is_still_working():
    # The steps come from the agent's report; marking mid-turn would present a thing that does
    # not exist yet.
    import pytest

    from entity.delivery import DeliveryError

    hold = threading.Event()
    desk, _, made = _desk(hold=hold)
    desk.start("fixer", "/wt/fixer", "a task")
    assert _wait_for(lambda: made and made[0].messages)

    with pytest.raises(DeliveryError):
        desk.present("fixer", "steps")
    hold.set()
    desk.close()


def test_presenting_an_agent_the_desk_does_not_have_refuses():
    import pytest

    from entity.delivery import DeliveryError

    desk, _, _ = _desk()

    with pytest.raises(DeliveryError):
        desk.present("nobody", "steps")


def test_an_approved_verdict_dispatches_the_landing():
    # After the user signs off, everything left is mechanical: the desk itself sends the agent
    # to land the work - no one has to remember to ask.
    desk, outbox, made = _desk()
    _finished(desk, outbox)
    desk.present("fixer", "steps")

    desk.verdict("fixer", approved=True)

    assert _wait_for(lambda: len(made[0].messages) == 2)
    assert "signed off" in made[0].messages[1]
    assert "approved, landing it" in desk.digest()
    desk.close()


def test_a_rejected_verdict_carries_the_feedback_back():
    desk, outbox, made = _desk()
    _finished(desk, outbox)
    desk.present("fixer", "steps")

    desk.verdict("fixer", approved=False, feedback="The button is on the wrong side.")

    assert _wait_for(lambda: len(made[0].messages) == 2)
    assert "The button is on the wrong side." in made[0].messages[1]
    assert "awaiting their verdict" not in desk.digest()  # back to plain being-built
    desk.close()


def test_a_verdict_with_no_presentation_is_refused_by_the_desk():
    import pytest

    from entity.delivery import DeliveryError

    desk, outbox, _ = _desk()
    _finished(desk, outbox)

    with pytest.raises(DeliveryError):
        desk.verdict("fixer", approved=True)
    desk.close()


def test_the_delivery_stage_survives_into_the_state_file_and_back(tmp_path):
    # A restart mid-loop must not lose where the work stood: presented work is still presented,
    # its steps still on file, when the next process revives the fleet.
    import json

    state = tmp_path / "agents.json"
    desk, outbox, _ = _desk(state=state)
    _finished(desk, outbox)
    desk.present("fixer", "Open localhost:5300.")
    desk.close()

    [entry] = json.loads(state.read_text(encoding="utf-8"))
    assert entry["delivery"] == "ready"
    assert entry["steps"] == "Open localhost:5300."

    reborn, _, _ = _desk(state=state)
    reborn.revive()
    assert "presented, awaiting their verdict" in reborn.digest()
    assert reborn.delivery_stage("fixer") == "ready"
    reborn.close()


def test_the_narrator_can_ask_which_stage_an_agent_is_at():
    desk, outbox, _ = _desk()
    assert desk.delivery_stage("fixer") is None  # unknown agent: no stage, not an error
    _finished(desk, outbox)
    desk.present("fixer", "steps")
    desk.verdict("fixer", approved=True)

    assert desk.delivery_stage("fixer") == "landing"
    desk.close()


def test_the_desk_can_say_what_an_agent_is_working_on():
    desk, outbox, _ = _desk()
    _finished(desk, outbox, task="fix the drive link")

    assert desk.task_of("fixer") == "fix the drive link"
    assert desk.task_of("nobody") is None
    desk.close()


def test_the_desk_hands_over_an_agents_recent_log_for_a_senior_read(tmp_path):
    # The foreman judges from what actually happened, and the log is where that lives. The tail,
    # not the whole file: a day-long exchange would drown the situation it ends on.
    desk, outbox, _ = _desk(log_dir=tmp_path)
    _finished(desk, outbox)

    tail = desk.recent_log("fixer")

    assert "did: a task" in tail  # the exchange the fake agent streamed is in the tail
    assert desk.recent_log("nobody") == ""
    desk.close()


def test_the_standing_rule_carries_the_engineering_law_not_just_the_review_law():
    # "how to TDD etc. is ultra critical" - and an agent may work in a repo whose CLAUDE.md is
    # thin or missing, so the discipline rides with the task itself: test-driven, full suite
    # green, land through the repo's own process, leave it cleaner.
    desk, outbox, made = _desk()
    desk.start("fixer", "/wt/fixer", "a task")
    assert _wait_for(lambda: bool(outbox))

    [task] = made[0].messages
    assert "test-drive" in task
    assert "full test suite" in task
    assert "merge queue" in task
    assert "CLAUDE.md" in task
    desk.close()


def test_every_task_points_the_agent_at_the_machine_wide_engineering_law(tmp_path):
    # "why wouldn't that be in the global CLAUDE.md?" - it is, and agents can't load that file
    # (its conversation rules break them). The engineering half now lives in its own file, and
    # every task points there: one source, read fresh by each agent, never pasted stale.
    law = tmp_path / "engineering.md"
    law.write_text("# engineering law", encoding="utf-8")
    desk, outbox, made = _desk(law=law)

    desk.start("fixer", "/wt/fixer", "a task")

    assert _wait_for(lambda: bool(outbox))
    assert str(law) in made[0].messages[0]
    assert "engineering law" in made[0].messages[0]
    desk.close()


def test_a_law_file_that_is_not_there_adds_no_pointer(tmp_path):
    # A checkout without the split (another machine, a fresh clone) must not send agents chasing
    # a file that does not exist.
    desk, outbox, made = _desk(law=tmp_path / "missing.md")

    desk.start("fixer", "/wt/fixer", "a task")

    assert _wait_for(lambda: bool(outbox))
    assert "missing.md" not in made[0].messages[0]
    desk.close()
