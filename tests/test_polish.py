import threading
import time

from entity.polish import Polisher, word_safe


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


def test_a_repair_that_eats_a_word_is_refused_by_code():
    # The model may fix a mishearing, but whatever it answers it is UNABLE to eat a word.
    raw = "add the risque option. To the dropdown"
    polisher, _ = _polisher(reply="add an option to the dropdown")

    assert polisher.polish(raw) == raw


def test_a_repair_may_fix_an_obvious_mishearing():
    # "I'd like the repair layer to be able to correct things like 'Maine' which is obviously
    # supposed to be `main` because we're doing software development, not tourism."
    raw = "The way you said that previously. implied that it was already on Maine."
    repaired = "The way you said that previously implied that it was already on main."
    polisher, _ = _polisher(reply=repaired)

    assert polisher.polish(raw) == repaired


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


def test_word_safe_sees_through_punctuation_but_not_through_words():
    assert word_safe("I think. We should go", "I think we should go!") is True
    assert word_safe("I think we should go", "I think we should stay") is False


def test_word_safe_allows_a_respelling_and_nothing_looser():
    # A mishearing is the same word in different letters; anything further is a different word.
    assert word_safe("push it to Maine", "push it to main") is True
    assert word_safe("send the Jason file", "send the JSON file") is True
    assert word_safe("push it to Maine", "push it to production") is False  # replaced outright
    assert word_safe("push it to Maine", "push to Maine") is False          # a word eaten
    assert word_safe("push it to Maine", "push it up to Maine") is False    # a word invented
    assert word_safe("push it to Maine", "to Maine push it") is False       # reordered


def test_the_background_repair_makes_the_submit_instant():
    # "ideally something is already working in the background while I'm speaking" - each pause
    # hands the draft-so-far to the polisher, so by submit the repair is usually already done
    # and the turn does not wait out a model call at all.
    import time

    raw = "I think we should. Rename the button"
    repaired = "I think we should rename the button"
    polisher, session = _polisher(reply=repaired)

    polisher.precook(raw)
    for _ in range(100):
        if polisher._precooked:
            break
        time.sleep(0.01)

    start = time.monotonic()
    assert polisher.polish(raw) == repaired
    assert time.monotonic() - start < 0.2  # served from the finished background repair
    assert raw in session.asked[0]


def test_a_tail_spoken_after_the_background_repair_still_lands():
    # He keeps talking after the last background pass: the cached head is used as repaired and
    # only the new tail goes to the model, bounded.
    import time

    head_raw = "I think we should. Rename the button"
    head_repaired = "I think we should rename the button"

    class TwoAnswers:
        def __init__(self):
            self.replies = [head_repaired, "and also the icon."]
            self.asked = []

        def ask(self, prompt, on_message=None, on_text=None):
            self.asked.append(prompt)
            return self.replies[min(len(self.asked) - 1, 1)]

    session = TwoAnswers()
    polisher = Polisher(session_factory=lambda options: session, deadline=1.0)
    polisher.precook(head_raw)
    for _ in range(100):
        if polisher._precooked:
            break
        time.sleep(0.01)

    out = polisher.polish(head_raw + " and also the Icon.")

    assert out == f"{head_repaired} and also the icon."


def test_an_edited_draft_falls_back_to_the_whole_bounded_repair():
    import time

    polisher, session = _polisher(reply="entirely different words here")
    polisher.precook("what was. Dictated first")
    for _ in range(100):
        if polisher._precooked:
            break
        time.sleep(0.01)

    out = polisher.polish("he rewrote the whole box by hand")

    assert out == "he rewrote the whole box by hand"  # refused repair -> his words as typed
