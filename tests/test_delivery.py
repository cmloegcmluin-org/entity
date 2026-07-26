import pytest

from entity.delivery import Delivery, DeliveryError


def test_new_work_is_being_built():
    assert Delivery().stage == "building"
    assert Delivery().steps is None


def test_presenting_records_the_steps_and_awaits_a_verdict():
    # "Ready for review" means the user can SEE it running. The steps are the proof there is
    # something to see - stored by code, so "how do I look at it again?" never depends on anyone's
    # memory of a sentence spoken an hour ago.
    work = Delivery()

    work.present("Open localhost:5300 and click the new Export button.")

    assert work.stage == "ready"
    assert "localhost:5300" in work.steps


def test_approval_sends_the_work_off_to_land():
    work = Delivery()
    work.present("steps")

    work.verdict(approved=True)

    assert work.stage == "landing"


def test_rejection_returns_the_work_to_the_bench_without_stale_steps():
    # Rejected work comes back presented afresh - keeping the old steps would let the loop show
    # yesterday's instructions for today's changed behavior.
    work = Delivery()
    work.present("old steps")

    work.verdict(approved=False)

    assert work.stage == "building"
    assert work.steps is None


def test_no_verdict_can_land_on_work_never_presented():
    # The guarantee the whole module exists for: on a bad day the model used to be able to skip
    # the show-me step entirely. Now the order is a rule, not a habit.
    with pytest.raises(DeliveryError):
        Delivery().verdict(approved=True)


def test_re_presenting_refreshes_the_steps():
    work = Delivery()
    work.present("first attempt")

    work.present("second attempt, port moved")

    assert work.stage == "ready"
    assert work.steps == "second attempt, port moved"


def test_work_already_landing_cannot_be_presented_again():
    work = Delivery()
    work.present("steps")
    work.verdict(approved=True)

    with pytest.raises(DeliveryError):
        work.present("newer steps")


def test_work_already_landing_takes_no_second_verdict():
    work = Delivery()
    work.present("steps")
    work.verdict(approved=True)

    with pytest.raises(DeliveryError):
        work.verdict(approved=False)


def test_the_briefing_phrase_says_what_the_user_is_waiting_on():
    work = Delivery()
    assert work.describe() is None  # plain being-built is the normal case; no noise for it

    work.present("steps")
    assert work.describe() == "presented, awaiting their verdict"

    work.verdict(approved=True)
    assert work.describe() == "approved, landing it"
