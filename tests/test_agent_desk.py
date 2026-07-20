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


def _desk(outbox=None, made=None, hold=None, roster=None, monitor=None):
    outbox = outbox or Outbox()
    made = made if made is not None else []

    def factory(name, cwd, decide):
        agent = FakeAgent(name, cwd, decide, hold=hold)
        made.append(agent)
        return agent

    return (AgentDesk(outbox, agent_factory=factory, roster_path=roster, monitor=monitor),
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
    desk = AgentDesk(Outbox(), agent_factory=lambda *a: _DyingAgent(), monitor=monitor)
    desk.start("doomed", "/tmp/wt", "try")

    assert _wait_for(lambda: monitor.finished == ["doomed"])


class _DyingAgent:
    def work(self, message, on_message=None):
        raise RuntimeError("session lost")

    def close(self):
        pass


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
    assert sent.startswith("fix the drive link")  # his ask first; the rule stands after it
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

    desk = AgentDesk(outbox, agent_factory=lambda name, cwd, decide: Exploding())

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

    def factory(name, cwd, decide):
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

    desk = AgentDesk(outbox, agent_factory=lambda name, cwd, decide: NarratingAgent(),
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

    desk = AgentDesk(outbox, agent_factory=lambda name, cwd, decide: Working(), log_dir=tmp_path)
    desk.start("fixer", "/tmp/wt", "make it green")
    assert _wait_for(lambda: bool(outbox))

    log = (tmp_path / "fixer.log").read_text(encoding="utf-8")
    assert "WORK> Bash: python -m pytest -q" in log
    assert "WORK>     358 passed in 4.41s" in log
    assert "AGENT> Green. Committing." in log
    desk.close()


def test_the_roster_says_when_each_agent_was_last_heard_from(tmp_path):
    outbox = Outbox()
    roster = tmp_path / "active-agents.txt"

    class NarratingAgent:
        def work(self, message, on_message=None):
            on_message(said("Reading the router."))
            return "done"

        def close(self):
            pass

    desk = AgentDesk(outbox, agent_factory=lambda name, cwd, decide: NarratingAgent(),
                     roster_path=roster, log_dir=tmp_path, clock=lambda fmt: "2026-07-19 08:20:15")
    desk.start("fixer", "/tmp/wt", "do the thing")
    assert _wait_for(lambda: bool(outbox))

    assert "last heard 2026-07-19 08:20:15" in roster.read_text(encoding="utf-8")
    desk.close()
