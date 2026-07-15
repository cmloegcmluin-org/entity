import asyncio
import threading
import time

from entity.fleet import FleetSupervisor
from entity.fleet_runner import Fleet, describe_request


def test_describe_request_renders_plain_language():
    assert describe_request("Bash", {"command": "npm test"}) == "run: npm test"
    assert describe_request("Edit", {"file_path": "web.py"}) == "edit web.py"
    assert describe_request("Write", {"file_path": "new.py"}) == "write new.py"
    assert describe_request("Read", {"file_path": "notes.md"}) == "read notes.md"
    assert describe_request("Grep", {"pattern": "x"}) == "use Grep"


def test_decide_raises_a_hand_then_blocks_until_you_answer():
    fleet = Fleet(FleetSupervisor())
    result = {}

    def run_decide():
        result["approved"] = asyncio.run(fleet.decide("the-tracker-note", "Bash", {"command": "npm test"}))

    worker = threading.Thread(target=run_decide)
    worker.start()

    # the agent's request should surface as a waiting need, phrased for the user
    deadline = time.time() + 2
    while not fleet.waiting() and time.time() < deadline:
        time.sleep(0.01)
    assert [n.agent for n in fleet.waiting()] == ["the-tracker-note"]
    assert fleet.waiting()[0].request == "run: npm test"
    assert not result  # still blocked, waiting on you

    fleet.answer("the-tracker-note", True)
    worker.join(timeout=2)

    assert result["approved"] is True
    assert fleet.waiting() == []  # resolved


def test_answering_an_unknown_agent_is_a_harmless_noop():
    fleet = Fleet(FleetSupervisor())

    fleet.answer("nobody", True)  # must not raise


def test_pick_marks_you_busy_so_others_wait_without_interrupting():
    fleet = Fleet(FleetSupervisor())
    fleet._supervisor.raise_hand("a", "q-a")
    fleet._supervisor.raise_hand("b", "q-b")

    fleet.pick("a")

    assert [n.agent for n in fleet.waiting()] == ["b"]  # A hidden while handled, B waits
