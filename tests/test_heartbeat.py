from entity.heartbeat import HeartbeatMonitor, _is_nothing


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

    HeartbeatMonitor(brain, outbox).poll_once()

    assert outbox.pushed == ["The auth agent needs your call: JWT or sessions?"]


def test_a_nothing_reply_is_not_pushed():
    for reply in ["nothing", "Nothing.", "  NOTHING!  "]:
        outbox = FakeOutbox()
        HeartbeatMonitor(FakeBrain(reply), outbox).poll_once()
        assert outbox.pushed == []


def test_the_poll_does_not_pollute_the_conversation_memory():
    brain = FakeBrain("nothing")

    HeartbeatMonitor(brain, FakeOutbox()).poll_once()

    assert brain.asks[0][1] is False  # asked with remember=False, so it stays out of the recent window


def test_a_brain_error_during_a_poll_is_swallowed():
    class BoomBrain:
        def respond(self, prompt, *, remember=True):
            raise RuntimeError("wedged session")

    outbox = FakeOutbox()
    HeartbeatMonitor(BoomBrain(), outbox).poll_once()  # a background check must never crash the app

    assert outbox.pushed == []


def test_is_nothing_recognizes_the_negative_reply():
    assert _is_nothing("nothing") and _is_nothing("Nothing.") and _is_nothing("  NOTHING! ")
    assert not _is_nothing("the deploy agent finished")
