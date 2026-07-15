from entity.fleet import Need
from entity.fleet_session import drive_fleet


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

    def pick(self, names):
        return self._picks.pop(0)

    def approve(self, agent, request):
        return self._approvals.pop(0)

    def announce(self, text):
        self.announced.append(text)


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
