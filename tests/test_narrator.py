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


def test_a_finished_narration_is_told_that_tests_are_never_his_verification():
    # The narrated heads-up once told him to "run pytest in the worktree to verify" - the exact
    # thing his standing profile forbids. The prompt now carries the law where the wording is made.
    brain, outbox = FakeBrain(), Outbox()
    Narrator(brain, outbox).tell("finished", "fixer", "Done, PR open, 621 tests passing.")

    assert _wait_for(outbox)
    [(asked, _)] = brain.asked
    assert "see-it-running" in asked
    assert "never their verification" in asked


def test_a_finished_narration_may_kick_the_agent_itself_instead_of_interrupting():
    # An agent pausing to narrate an unactionable step is not news. The prompt offers the brain a
    # third way: nudge the agent onward with tell_agent and answer only "handled".
    brain, outbox = FakeBrain(), Outbox()
    Narrator(brain, outbox).tell("finished", "fixer", "I'll now run the tests, then continue.")

    assert _wait_for(outbox)
    [(asked, _)] = brain.asked
    assert "tell_agent" in asked
    assert "handled" in asked


def test_a_handled_reply_is_swallowed_and_the_user_hears_nothing():
    # When the brain kicked the agent onward itself, there is no news: pushing "Handled." to the
    # outbox would interrupt the user with a word about nothing.
    responded = threading.Event()

    class KickingBrain(FakeBrain):
        def respond(self, utterance, *, remember=True, on_text=None):
            try:
                return super().respond(utterance, remember=remember, on_text=on_text)
            finally:
                responded.set()

    outbox = Outbox()
    Narrator(KickingBrain("Handled."), outbox).tell("finished", "fixer", "Continuing shortly.")

    assert responded.wait(2.0)
    settled = threading.Event()
    for _ in range(20):  # give the push after respond() every chance to happen if it wrongly would
        if outbox:
            break
        settled.wait(0.01)
    assert not outbox


def test_a_finished_agent_that_was_landing_approved_work_gets_the_wrap_up_prompt():
    # After the user approves, the rest is mechanical: the agent lands it, and the brain is told
    # to wrap the agent up itself the moment the report says it merged - not to hand the user
    # another chore.
    brain, outbox = FakeBrain(), Outbox()
    narrator = Narrator(brain, outbox, stage_of=lambda name: "landing")

    narrator.tell("finished", "fixer", "Merged - the queue took it, main has the work.")

    assert _wait_for(outbox)
    [(asked, _)] = brain.asked
    assert "close_agent_tab" in asked
    assert "approved" in asked


def test_a_finished_agent_still_building_keeps_the_presentation_prompt():
    brain, outbox = FakeBrain(), Outbox()
    narrator = Narrator(brain, outbox, stage_of=lambda name: "building")

    narrator.tell("finished", "fixer", "Done with the first pass.")

    assert _wait_for(outbox)
    [(asked, _)] = brain.asked
    assert "see-it-running" in asked
    assert "close_agent_tab" not in asked


def test_a_finished_narration_knows_the_foreman_exists_for_technical_snags():
    # "a smarter Claude agent would take care of negotiating issues that come up with the working
    # agents" - the brain is the router, so the option has to be in front of it where the wording
    # is made, or every snag still lands on the user.
    brain, outbox = FakeBrain(), Outbox()
    Narrator(brain, outbox).tell("finished", "fixer", "I need to know which auth library to use.")

    assert _wait_for(outbox)
    [(asked, _)] = brain.asked
    assert "ask_foreman" in asked


def test_a_quiet_narration_offers_the_foreman_as_the_prod():
    brain, outbox = FakeBrain(), Outbox()
    Narrator(brain, outbox).tell("quiet", "fixer", "been silent for 25 minutes")

    assert _wait_for(outbox)
    [(asked, _)] = brain.asked
    assert "ask_foreman" in asked
