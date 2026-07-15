"""Run a fleet of supervised agents and let the user manage them by voice.

`run_fleet` launches each agent working on its task (one thread apiece) and then hands off to
`drive_fleet`, the loop that watches for raised hands and relays them to the user: when several
are ready it asks him which to take (non-FIFO), tells him what that one wants, takes his yes/no,
and relays it back — never interrupting while he's mid-answer. `io` is how it talks to him
(voice or console), injected so the loop is testable without either.
"""

import threading
import time
from pathlib import Path

TASK = (
    "You are in a git worktree. Look at the branch name and the working tree, work out what "
    "this session is meant to be doing, and continue it. You'll be asked to approve anything that "
    "changes files or runs commands, so go ahead and propose your next action."
)


def find_worktrees(directory):
    """The immediate sub-directories of `directory` (each a worktree). Empty if it isn't a dir."""
    path = Path(directory).expanduser()
    if not path.is_dir():
        return []
    return sorted(str(child) for child in path.iterdir() if child.is_dir())


def supervise(worktree_paths, io, *, model="sonnet", task=TASK, agent_factory=None):
    """Launch a supervised agent per worktree path and manage them through `io`."""
    from entity.fleet import FleetSupervisor
    from entity.fleet_runner import Fleet
    from entity.supervised_agent import SupervisedAgent

    make_agent = agent_factory or (lambda name, cwd, decide: SupervisedAgent(name, cwd, decide, model=model))
    fleet = Fleet(FleetSupervisor())
    agents = {Path(p).name: make_agent(Path(p).name, p, fleet.decide) for p in worktree_paths}
    tasks = {name: task for name in agents}
    try:
        return run_fleet(agents, tasks, fleet, io)
    finally:
        for agent in agents.values():
            try:
                agent.close()
            except Exception:
                pass


def drive_fleet(fleet, io, still_working, *, poll=0.1):
    while still_working() or fleet.waiting():
        ready = fleet.waiting()
        if not ready:
            time.sleep(poll)
            continue
        names = [need.agent for need in ready]
        choice = names[0] if len(names) == 1 else io.pick(names)
        need = fleet.pick(choice)
        approved = io.approve(choice, need.request)
        fleet.answer(choice, approved)


def run_fleet(agents, tasks, fleet, io):
    """agents: {name: SupervisedAgent}, tasks: {name: prompt}. Returns each agent's final report."""
    reports = {}
    threads = {}

    def worker(name, agent):
        try:
            reports[name] = agent.work(tasks[name])
        except Exception as exc:  # a dead agent shouldn't strand the rest of the fleet
            reports[name] = f"(failed: {exc})"

    for name, agent in agents.items():
        thread = threading.Thread(target=worker, args=(name, agent), daemon=True)
        thread.start()
        threads[name] = thread

    io.announce(f"Started {len(agents)} agents. I'll speak up when one needs you.")
    drive_fleet(fleet, io, still_working=lambda: any(t.is_alive() for t in threads.values()))
    for name in agents:
        io.report(name, reports.get(name, "(no report)"))
    return reports
