import threading
import time

from entity.agent_desk import AgentDesk
from entity.outbox import Outbox


class FakeAgent:
    """Stands in for a real SupervisedAgent: a persistent session that remembers its messages."""

    def __init__(self, name, cwd, decide, hold=None):
        self.name = name
        self.cwd = cwd
        self.decide = decide
        self.messages = []
        self.closed = False
        self._hold = hold

    def work(self, message, on_step=None):
        self.messages.append(message)
        if on_step is not None:
            on_step(f"[{self.name}] did: {message}")
        if self._hold is not None:
            self._hold.wait(2.0)
        return f"[{self.name}] did: {message}"

    def close(self):
        self.closed = True


def _desk(outbox=None, made=None, hold=None, roster=None):
    outbox = outbox or Outbox()
    made = made if made is not None else []

    def factory(name, cwd, decide):
        agent = FakeAgent(name, cwd, decide, hold=hold)
        made.append(agent)
        return agent

    return AgentDesk(outbox, agent_factory=factory, roster_path=roster), outbox, made


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


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


def test_a_follow_up_reaches_the_same_agent_not_a_new_one():
    # Four agents in a row were lost because there was no live handle to talk back to.
    desk, outbox, made = _desk()
    desk.start("fixer", "/tmp/wt", "first task")
    assert _wait_for(lambda: bool(outbox))
    outbox.drain()

    assert desk.send("fixer", "now do the other half")

    assert _wait_for(lambda: bool(outbox))
    assert len(made) == 1  # the same agent, not a fresh one
    assert made[0].messages == ["first task", "now do the other half"]
    desk.close()


def test_a_follow_up_to_an_agent_that_was_never_started_says_so():
    desk, _, made = _desk()

    assert desk.send("ghost", "you there?") is False
    assert made == []


def test_an_agent_that_blows_up_is_reported_not_swallowed():
    outbox = Outbox()

    class Exploding:
        def work(self, message, on_step=None):
            raise RuntimeError("session died")

        def close(self):
            pass

    desk = AgentDesk(outbox, agent_factory=lambda name, cwd, decide: Exploding())

    desk.start("doomed", "/tmp/wt", "do a thing")

    assert _wait_for(lambda: bool(outbox))
    assert any("doomed" in m and "session died" in m for m in outbox.drain())
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
        def work(self, message, on_step=None):
            for step in steps:
                on_step(step)
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


def test_the_roster_says_when_each_agent_was_last_heard_from(tmp_path):
    outbox = Outbox()
    roster = tmp_path / "active-agents.txt"

    class NarratingAgent:
        def work(self, message, on_step=None):
            on_step("Reading the router.")
            return "done"

        def close(self):
            pass

    desk = AgentDesk(outbox, agent_factory=lambda name, cwd, decide: NarratingAgent(),
                     roster_path=roster, log_dir=tmp_path, clock=lambda fmt: "2026-07-19 08:20:15")
    desk.start("fixer", "/tmp/wt", "do the thing")
    assert _wait_for(lambda: bool(outbox))

    assert "last heard 2026-07-19 08:20:15" in roster.read_text(encoding="utf-8")
    desk.close()
