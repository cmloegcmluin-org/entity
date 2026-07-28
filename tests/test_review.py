import threading

from entity.review import MemoryNudger


class Sink:
    def __init__(self):
        self.raised = []

    def __call__(self, kind, agent, report):
        self.raised.append((kind, report))


def _nudger(sink, memories, *, idle=True, quiet=999.0, clock=None):
    beat = {"now": 0.0}
    return MemoryNudger(sink, memories=memories, fleet_idle=lambda: idle,
                        quiet_for=lambda: quiet,
                        clock=clock or (lambda: beat["now"])), beat


def test_downtime_raises_one_memory_and_only_one():
    # "this list is an inbox, and I'm an inbox-0 kind of guy" - but one at a time: an inbox that
    # dumps itself in a lull is a wall, not a review.
    sink = Sink()
    nudger, _ = _nudger(sink, lambda: ["fact one", "fact two"])

    nudger.poll_once()

    assert sink.raised == [("memory", "fact one")]


def test_the_gap_holds_the_next_memory_back_and_then_releases_it():
    sink = Sink()
    nudger, beat = _nudger(sink, lambda: ["fact one", "fact two"])

    nudger.poll_once()
    beat["now"] = 30.0
    nudger.poll_once()          # too soon: the last nudge was half a minute ago
    assert len(sink.raised) == 1

    beat["now"] = 700.0
    nudger.poll_once()
    assert sink.raised[-1] == ("memory", "fact two")


def test_each_memory_is_raised_once_per_session():
    sink = Sink()
    nudger, beat = _nudger(sink, lambda: ["the only fact"])

    nudger.poll_once()
    beat["now"] = 2000.0
    nudger.poll_once()   # reviewed or not, it is not brought up again this session

    assert len(sink.raised) == 1


def test_working_agents_or_a_live_conversation_hold_the_nudge_entirely():
    sink = Sink()
    busy, _ = _nudger(sink, lambda: ["fact"], idle=False)
    busy.poll_once()
    talking, _ = _nudger(sink, lambda: ["fact"], quiet=10.0)
    talking.poll_once()

    assert sink.raised == []
