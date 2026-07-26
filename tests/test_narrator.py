import threading

from entity.narrator import Narrator
from entity.outbox import Outbox


class FakeBrain:
    def __init__(self, reply="The drive work's done - it just needs your eyes."):
        self._reply = reply
        self.asked = []

    def respond(self, utterance, *, remember=True, on_text=None):
        self.asked.append((utterance, remember))
        return self._reply


def _wait_for(outbox, timeout=2.0):
    deadline = threading.Event()
    for _ in range(int(timeout / 0.01)):
        if outbox:
            return True
        deadline.wait(0.01)
    return bool(outbox)


def test_news_reaches_the_outbox_in_the_brains_own_words():
    # "gdoc-export: HIGHDEAS: native Google Doc export (branch ...) — update: ..." is a log line
    # wearing a voice, and he said so. The brain reads the agent's report and composes the one or
    # two sentences he actually hears - so the interjection is the same voice he talks to.
    brain, outbox = FakeBrain(), Outbox()
    narrator = Narrator(brain, outbox)

    narrator.tell("finished", "fixer", "All six tasks are done. Full suite green, pushed.")

    assert _wait_for(outbox)
    [news] = outbox.drain()
    assert str(news) == "The drive work's done - it just needs your eyes."
    assert news.about == "fixer"
    assert news.composed is True  # the brain wrote it, so nothing need be read back to it


def test_the_brain_is_told_which_agent_and_what_it_reported():
    brain, outbox = FakeBrain(), Outbox()
    Narrator(brain, outbox).tell("finished", "fixer", "Done. 621 passing, not merged.")

    assert _wait_for(outbox)
    [(asked, remembered)] = brain.asked
    assert "fixer" in asked
    assert "621 passing" in asked
    assert remembered is True  # he will refer to this later; it belongs in the brain's thread


def test_a_death_is_narrated_as_what_it_is():
    brain, outbox = FakeBrain("The fixer agent died mid-task - want me to start a fresh one?"), Outbox()
    Narrator(brain, outbox).tell("died", "fixer", "RuntimeError: session lost")

    assert _wait_for(outbox)
    [(asked, _)] = brain.asked
    assert "died" in asked.lower()


def test_a_brain_failure_falls_back_to_the_plain_notice():
    # News must never die with the brain: a narration that cannot be composed is still delivered,
    # as the capped first-sentence notice the relay has always made.
    class BrokenBrain:
        def respond(self, utterance, *, remember=True, on_text=None):
            raise RuntimeError("session wedged")

    outbox = Outbox()
    Narrator(BrokenBrain(), outbox).tell("finished", "fixer", "All done. Extra detail here.")

    assert _wait_for(outbox)
    [news] = outbox.drain()
    assert "fixer" in str(news) and "All done." in str(news)
    assert news.composed is False  # app-authored after all, so the ledger treats it as unwritten


def test_tell_returns_at_once_and_narrates_off_thread():
    started = threading.Event()
    finished = threading.Event()

    class SlowBrain:
        def respond(self, utterance, *, remember=True, on_text=None):
            started.set()
            finished.wait(2.0)
            return "done now"

    outbox = Outbox()
    Narrator(SlowBrain(), outbox).tell("finished", "fixer", "report")

    assert started.wait(2.0)  # the narration is underway...
    assert not outbox  # ...but tell() already returned without blocking on it
    finished.set()
    assert _wait_for(outbox)
