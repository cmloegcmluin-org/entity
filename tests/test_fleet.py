from entity.fleet import FleetSupervisor, Need


def test_an_agent_raising_its_hand_shows_up_as_waiting():
    fleet = FleetSupervisor()

    fleet.raise_hand("notecraft-1", "Should the trash icon confirm before deleting?")

    assert fleet.waiting() == [Need("notecraft-1", "Should the trash icon confirm before deleting?")]
    assert fleet.is_free


def test_several_can_wait_and_you_pick_any_of_them_not_fifo():
    fleet = FleetSupervisor()
    fleet.raise_hand("a", "q-a")
    fleet.raise_hand("b", "q-b")
    fleet.raise_hand("c", "q-c")

    assert {n.agent for n in fleet.waiting()} == {"a", "b", "c"}

    picked = fleet.pick("b")  # you choose the middle one, not the first

    assert picked == Need("b", "q-b")
    assert fleet.current == "b"
    assert not fleet.is_free


def test_new_needs_wait_silently_while_you_handle_one():
    fleet = FleetSupervisor()
    fleet.raise_hand("a", "q-a")
    fleet.pick("a")

    fleet.raise_hand("b", "q-b")  # another finishes mid-conversation — no interruption

    assert fleet.current == "a"  # still with A
    assert fleet.waiting() == [Need("b", "q-b")]  # B waits, A is hidden (being handled)


def test_resolving_frees_you_and_leaves_the_rest_waiting():
    fleet = FleetSupervisor()
    fleet.raise_hand("a", "q-a")
    fleet.raise_hand("b", "q-b")
    fleet.pick("a")

    fleet.resolve("a")

    assert fleet.is_free
    assert fleet.waiting() == [Need("b", "q-b")]


def test_an_agent_asking_again_keeps_a_single_latest_need():
    fleet = FleetSupervisor()
    fleet.raise_hand("a", "first question")
    fleet.raise_hand("a", "actually, this question")

    assert fleet.waiting() == [Need("a", "actually, this question")]
