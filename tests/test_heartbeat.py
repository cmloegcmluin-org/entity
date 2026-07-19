from entity.heartbeat import HeartbeatMonitor, _is_nothing
from entity.outbox import Outbox


def _monitor(brain, outbox, **kwargs):
    """A monitor with one agent running - the only state in which it polls at all."""
    kwargs.setdefault("roster", lambda: ["auth"])
    return HeartbeatMonitor(brain, outbox, **kwargs)


class FakeBrain:
    def __init__(self, reply):
        self._reply = reply
        self.asks = []

    def respond(self, prompt, *, remember=True):
        self.asks.append((prompt, remember))
        return self._reply


class FakeOutbox:
    def __init__(self):
        self.pushed = []

    def push(self, message):
        self.pushed.append(message)


def test_new_agent_news_is_pushed_to_the_outbox():
    brain = FakeBrain("The auth agent needs your call: JWT or sessions?")
    outbox = FakeOutbox()

    _monitor(brain, outbox).poll_once()

    assert outbox.pushed == ["The auth agent needs your call: JWT or sessions?"]


def test_a_nothing_reply_is_not_pushed():
    for reply in ["nothing", "Nothing.", "  NOTHING!  "]:
        outbox = FakeOutbox()
        _monitor(FakeBrain(reply), outbox).poll_once()
        assert outbox.pushed == []


def test_the_poll_does_not_pollute_the_conversation_memory():
    brain = FakeBrain("nothing")

    _monitor(brain, FakeOutbox()).poll_once()

    assert brain.asks[0][1] is False  # asked with remember=False, so it stays out of the recent window


def test_a_brain_error_during_a_poll_is_swallowed():
    class BoomBrain:
        def respond(self, prompt, *, remember=True):
            raise RuntimeError("wedged session")

    outbox = FakeOutbox()
    _monitor(BoomBrain(), outbox).poll_once()  # a background check must never crash the app

    assert outbox.pushed == []


def test_is_nothing_recognizes_the_negative_reply():
    assert _is_nothing("nothing") and _is_nothing("Nothing.") and _is_nothing("  NOTHING! ")
    assert not _is_nothing("the deploy agent finished")


def test_no_agents_means_no_poll_at_all():
    # With nothing running, "anything new from the agents you're running?" invites the brain to go
    # hunting - and it found day-old inbox files and announced them as fresh news he'd never asked
    # for. No agents, no question.
    class LoudBrain:
        def __init__(self):
            self.asked = []

        def respond(self, prompt, remember=True):
            self.asked.append(prompt)
            return "hungry-neumann finished the Drive work"

    brain = LoudBrain()
    outbox = Outbox()
    HeartbeatMonitor(brain, outbox, roster=lambda: []).poll_once()

    assert brain.asked == []  # never even asked
    assert not outbox


def test_the_poll_names_the_agents_actually_running():
    class NotingBrain:
        def __init__(self):
            self.asked = []

        def respond(self, prompt, remember=True):
            self.asked.append(prompt)
            return "nothing"

    brain = NotingBrain()
    HeartbeatMonitor(brain, Outbox(), roster=lambda: ["fixer", "helper"]).poll_once()

    assert "fixer" in brain.asked[0] and "helper" in brain.asked[0]  # grounded in the real roster
