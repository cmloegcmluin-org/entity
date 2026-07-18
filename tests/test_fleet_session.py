from pathlib import Path
from types import SimpleNamespace

from entity.fleet import FleetSupervisor, Need
from entity.fleet_runner import Fleet
from entity.fleet_session import drive_fleet, prepare_worktree, prepare_worktree_for, run_fleet, supervise


class FakeFleet:
    """Scripted waiting() states that advance each time you answer; records picks/answers."""

    def __init__(self, states):
        self._states = list(states)
        self._i = 0

    def waiting(self):
        return self._states[self._i] if self._i < len(self._states) else []

    def pick(self, agent):
        return next(n for n in self.waiting() if n.agent == agent)

    def answer(self, agent, approved):
        self._i += 1

    def still_working(self):
        return self._i < len(self._states) - 1


class FakeIO:
    def __init__(self, picks, approvals):
        self._picks = list(picks)
        self._approvals = list(approvals)
        self.announced = []
        self.reported = []

    def pick(self, names):
        return self._picks.pop(0)

    def approve(self, agent, request):
        return self._approvals.pop(0)

    def announce(self, text):
        self.announced.append(text)

    def report(self, agent, text):
        self.reported.append((agent, text))


class FakeLog:
    """Records the timestamped-transcript calls without touching a file."""

    def __init__(self):
        self.entries = []

    def entity(self, text):
        self.entries.append(("ENTITY", text))

    def agent(self, name, text):
        self.entries.append((f"AGENT {name}", text))


class FakeAgent:
    def __init__(self, name, report):
        self.name = name
        self._report = report

    def work(self, task):
        return self._report

    def close(self):
        pass


def test_drive_fleet_only_asks_you_to_pick_when_several_are_ready():
    states = [
        [Need("a", "run npm test"), Need("b", "edit web.py")],  # two ready -> you pick
        [Need("b", "edit web.py")],  # one ready -> handled automatically
        [],  # done
    ]
    fleet = FakeFleet(states)
    io = FakeIO(picks=["a"], approvals=[True, False])

    drive_fleet(fleet, io, still_working=fleet.still_working, poll=0)

    assert fleet._i == 2  # worked through both
    # you were asked to choose only when more than one was waiting
    assert io._picks == []


def test_drive_fleet_logs_each_request_and_the_decision():
    states = [[Need("a", "run npm test")], []]
    fleet = FakeFleet(states)
    io = FakeIO(picks=[], approvals=[True])
    log = FakeLog()

    drive_fleet(fleet, io, still_working=fleet.still_working, poll=0, log=log)

    assert ("AGENT a", "run npm test") in log.entries
    assert ("ENTITY", "a: approved") in log.entries


def test_drive_fleet_records_a_declined_request_as_denied():
    states = [[Need("a", "delete the database")], []]
    fleet = FakeFleet(states)
    io = FakeIO(picks=[], approvals=[False])
    log = FakeLog()

    drive_fleet(fleet, io, still_working=fleet.still_working, poll=0, log=log)

    assert ("ENTITY", "a: denied") in log.entries


def test_run_fleet_logs_the_opener_and_each_agents_report():
    agents = {"a": FakeAgent("a", "a is done"), "b": FakeAgent("b", "b is done")}
    tasks = {"a": "task", "b": "task"}
    fleet = Fleet(FleetSupervisor())  # no hands raised, so the drive loop just waits out the workers
    io = FakeIO(picks=[], approvals=[])
    log = FakeLog()

    run_fleet(agents, tasks, fleet, io, log=log)

    assert ("ENTITY", "Started 2 agents. I'll speak up when one needs you.") in log.entries
    assert ("AGENT a", "a is done") in log.entries
    assert ("AGENT b", "b is done") in log.entries


def test_prepare_worktree_fetches_before_branching_from_current_origin_main():
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))

    returned = prepare_worktree(
        "/repo", "/repo/.claude/worktrees/new-agent", "claude/new-agent", run=fake_run
    )

    assert calls[0][0] == ["git", "-C", "/repo", "fetch", "origin", "main"]
    assert calls[1][0] == [
        "git", "-C", "/repo", "worktree", "add", "-b",
        "claude/new-agent", "/repo/.claude/worktrees/new-agent", "origin/main",
    ]
    assert all(kwargs.get("check") for _, kwargs in calls)  # a git failure must raise, not slip by
    assert returned == "/repo/.claude/worktrees/new-agent"


def test_prepare_worktree_for_infers_the_repo_and_branch_then_cuts_fresh(tmp_path):
    worktrees = tmp_path / ".claude" / "worktrees"
    worktrees.mkdir(parents=True)  # exists; the new leaf below does not yet
    new = worktrees / "brave-swan"
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(stdout=f"{tmp_path}\n")  # git rev-parse answers with the repo root

    returned = prepare_worktree_for(str(new), run=fake_run)

    assert calls[0] == ["git", "-C", str(worktrees), "rev-parse", "--show-toplevel"]
    assert ["git", "-C", str(tmp_path), "fetch", "origin", "main"] in calls
    assert [
        "git", "-C", str(tmp_path), "worktree", "add", "-b", "claude/brave-swan", str(new), "origin/main"
    ] in calls
    assert returned == str(new)


def test_prepare_worktree_for_walks_up_to_the_first_existing_ancestor(tmp_path):
    # The very first worktree in a repo: neither the leaf nor .claude/worktrees exists, so the repo
    # root itself is where we ask git which repo this is.
    new = tmp_path / ".claude" / "worktrees" / "calm-lake"
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        return SimpleNamespace(stdout=f"{tmp_path}\n")

    prepare_worktree_for(str(new), run=fake_run)

    assert seen[0] == ["git", "-C", str(tmp_path), "rev-parse", "--show-toplevel"]


def test_supervise_cuts_a_fresh_worktree_by_default(tmp_path, monkeypatch):
    # Starting new worktrees is the norm, so supervise creates a missing one with no prepare step
    # handed in - it falls back to prepare_worktree_for. Stub that to keep the test repo-free.
    from entity import fleet_session

    fresh = tmp_path / "new-agent"  # doesn't exist yet
    prepared = []

    def spy(path):
        prepared.append(path)
        Path(path).mkdir(parents=True)

    monkeypatch.setattr(fleet_session, "prepare_worktree_for", spy)

    supervise(
        [str(fresh)],
        FakeIO(picks=[], approvals=[]),
        agent_factory=lambda name, cwd, decide: FakeAgent(name, "done"),
    )

    assert prepared == [str(fresh)]  # created fresh, though no prepare was passed


def test_supervise_creates_only_the_missing_worktree_then_launches_every_agent(tmp_path):
    here = tmp_path / "here"
    here.mkdir()
    fresh = tmp_path / "fresh"  # doesn't exist yet
    prepared = []
    made = []

    def fake_prepare(path):
        prepared.append(str(path))
        Path(path).mkdir()  # the fresh worktree now exists on disk

    def fake_agent_factory(name, cwd, decide):
        made.append(name)
        return FakeAgent(name, f"{name} done")

    supervise(
        [str(here), str(fresh)],
        FakeIO(picks=[], approvals=[]),
        agent_factory=fake_agent_factory,
        prepare=fake_prepare,
    )

    assert prepared == [str(fresh)]  # the existing worktree was left alone
    assert sorted(made) == ["fresh", "here"]  # both agents were launched
