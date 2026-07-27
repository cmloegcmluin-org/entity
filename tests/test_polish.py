import threading
import time

from entity.polish import Polisher, same_words


class FakeSession:
    def __init__(self, reply=None, hold=None):
        self._reply = reply
        self._hold = hold
        self.asked = []

    def ask(self, prompt, on_message=None, on_text=None):
        self.asked.append(prompt)
        if self._hold is not None:
            self._hold.wait(2.0)
        return self._reply


def _polisher(reply=None, hold=None, deadline=1.0):
    session = FakeSession(reply, hold=hold)
    return Polisher(session_factory=lambda options: session, deadline=deadline), session


def test_a_choppy_draft_comes_back_as_the_sentences_he_meant():
    # "plenty of natural pauses in my speech, and they are getting treated as sentence breaks."
    raw = "I think we should. Rename the button. Because it's confusing"
    repaired = "I think we should rename the button, because it's confusing"
    polisher, session = _polisher(reply=repaired)

    assert polisher.polish(raw) == repaired
    assert raw in session.asked[0]


def test_a_repair_that_changes_any_word_is_refused_by_code():
    # The model is ASKED to touch only punctuation; this is what makes it UNABLE to eat a word.
    raw = "add the risque option. To the dropdown"
    polisher, _ = _polisher(reply="add an option to the dropdown")

    assert polisher.polish(raw) == raw


def test_a_late_repair_lets_the_raw_text_through():
    # "hopefully only a second of wait time" - a hung model may never hold a turn hostage.
    hold = threading.Event()
    polisher, _ = _polisher(reply="never arrives", hold=hold, deadline=0.05)

    start = time.monotonic()
    out = polisher.polish("what he said. As he said it")

    assert out == "what he said. As he said it"
    assert time.monotonic() - start < 0.5
    hold.set()


def test_a_failed_session_lets_the_raw_text_through():
    class BrokenSession:
        def ask(self, prompt, on_message=None, on_text=None):
            raise RuntimeError("wedged")

    polisher = Polisher(session_factory=lambda options: BrokenSession(), deadline=1.0)

    assert polisher.polish("the words he typed") == "the words he typed"


def test_empty_drafts_never_wake_the_model():
    polisher, session = _polisher(reply="anything")

    assert polisher.polish("   ") == "   "
    assert session.asked == []


def test_one_warm_session_serves_every_submit():
    made = []
    session = FakeSession(reply="fine. And repaired")

    def factory(options):
        made.append(options)
        return session

    polisher = Polisher(session_factory=factory, deadline=1.0)
    polisher.warmup()
    polisher.polish("fine and repaired")
    polisher.polish("fine and repaired")

    assert len(made) == 1


def test_same_words_sees_through_punctuation_but_not_through_words():
    assert same_words("I think. We should go", "I think we should go!") is True
    assert same_words("I think we should go", "I think we should stay") is False
