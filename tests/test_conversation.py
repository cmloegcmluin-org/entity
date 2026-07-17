import time

from entity.conversation import DEFAULT_ACKS, Conversation, Turn, _make_picker
from entity.outbox import Outbox


class FakeSTT:
    def __init__(self, utterances):
        self._utterances = list(utterances)
        self.calls = 0

    def listen(self):
        self.calls += 1
        if not self._utterances:
            raise AssertionError("STT exhausted - the loop failed to stop")
        return self._utterances.pop(0)


class FakeBrain:
    def __init__(self):
        self.heard = []

    def respond(self, utterance):
        self.heard.append(utterance)
        return f"reply to {utterance}"


class FakeTTS:
    def __init__(self):
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)


def test_turn_transcribes_thinks_and_speaks():
    stt = FakeSTT(["hello"])
    brain = FakeBrain()
    tts = FakeTTS()
    convo = Conversation(stt, brain, tts, acknowledger=lambda: "ACK")

    turn = convo.turn()

    assert brain.heard == ["hello"]
    assert tts.spoken == ["ACK", "reply to hello"]  # heard-you beat first, then the real reply
    assert turn == Turn(heard="hello", said="reply to hello")


def test_a_real_turn_acknowledges_the_instant_it_hears_you_before_thinking():
    events = []

    class WatchfulBrain:
        def respond(self, utterance):
            events.append("think")
            return "reply"

    class WatchfulTTS:
        def speak(self, text):
            events.append(f"say:{text}")

    convo = Conversation(FakeSTT(["hi"]), WatchfulBrain(), WatchfulTTS(), acknowledger=lambda: "mm-hm")
    convo.turn()

    # the acknowledgement is spoken BEFORE the brain is even asked, so there's no dead air
    assert events == ["say:mm-hm", "think", "say:reply"]


def test_no_acknowledgement_for_blank_farewell_or_suspend():
    acks = []

    def spy_ack():
        acks.append(True)
        return "ACK"

    for utterance in ["   ", "goodbye entity", "suspend"]:
        convo = Conversation(FakeSTT([utterance]), FakeBrain(), FakeTTS(), acknowledger=spy_ack)
        convo.turn()

    assert acks == []  # nothing to think about, so nothing to acknowledge


def test_queued_agent_news_is_spoken_when_it_is_the_entitys_turn():
    outbox = Outbox()
    outbox.push("Heads up - the auth agent is ready for your review.")
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["goodbye entity"]), FakeBrain(), tts, outbox=outbox)

    convo.turn()

    assert tts.spoken[0] == "Heads up - the auth agent is ready for your review."  # before we listened


def test_several_queued_messages_are_all_delivered_in_order():
    outbox = Outbox()
    outbox.push("first")
    outbox.push("second")
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["goodbye entity"]), FakeBrain(), tts, outbox=outbox)

    convo.turn()

    assert tts.spoken[:2] == ["first", "second"]


def test_without_an_outbox_the_loop_is_unchanged():
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["hi"]), FakeBrain(), tts, acknowledger=lambda: "ACK")

    convo.turn()

    assert tts.spoken == ["ACK", "reply to hi"]  # outbox=None interposes nothing


def test_a_message_arriving_during_a_lull_is_spoken_on_the_next_pass():
    outbox = Outbox()

    class LullSTT:
        # the real MicSTT yields "" when its interrupt fires during a lull; mimic that here by
        # having a message land mid-lull and the listen break off empty.
        def __init__(self):
            self.n = 0

        def listen(self):
            self.n += 1
            if self.n == 1:
                outbox.push("the deploy agent hit an error")
                return ""
            return "goodbye entity"

    tts = FakeTTS()
    convo = Conversation(LullSTT(), FakeBrain(), tts, outbox=outbox)
    convo.run()

    assert tts.spoken == ["the deploy agent hit an error", convo.farewell_reply]


def test_default_picker_varies_and_never_repeats_back_to_back():
    import random

    pick = _make_picker(DEFAULT_ACKS, rng=random.Random(0))
    picks = [pick() for _ in range(40)]

    assert all(a in DEFAULT_ACKS for a in picks)
    assert all(picks[i] != picks[i - 1] for i in range(1, len(picks)))  # no immediate repeats
    assert len(set(picks)) > 1  # it actually varies


def test_a_slow_reply_speaks_a_reassurance_so_it_does_not_read_as_a_crash():
    class SlowBrain:
        def respond(self, utterance):
            time.sleep(0.15)  # comfortably longer than the tiny patience below
            return f"reply to {utterance}"

    tts = FakeTTS()
    convo = Conversation(
        FakeSTT(["hello"]), SlowBrain(), tts,
        acknowledger=lambda: "ACK", reassurer=lambda: "WAIT", patience=0.02,
    )
    convo.turn()

    assert tts.spoken == ["ACK", "WAIT", "reply to hello"]  # heard-you, then still-here, then reply


def test_a_quick_reply_gets_no_reassurance():
    tts = FakeTTS()
    convo = Conversation(
        FakeSTT(["hello"]), FakeBrain(), tts,
        acknowledger=lambda: "ACK", reassurer=lambda: "WAIT", patience=30,
    )
    convo.turn()

    assert tts.spoken == ["ACK", "reply to hello"]  # fast enough that "WAIT" never fires


def test_a_slow_brain_failure_still_surfaces_as_the_error_reply():
    class SlowBoom:
        def respond(self, utterance):
            time.sleep(0.05)
            raise RuntimeError("hiccup after a pause")

    tts = FakeTTS()
    convo = Conversation(
        FakeSTT(["hello"]), SlowBoom(), tts,
        acknowledger=lambda: "ACK", reassurer=lambda: "WAIT", patience=0.01,
    )
    turn = convo.turn()

    assert turn.error is True
    assert tts.spoken == ["ACK", "WAIT", convo.error_reply]  # the off-thread error is re-raised in context


def test_blank_utterance_is_skipped():
    stt = FakeSTT(["   "])
    brain = FakeBrain()
    tts = FakeTTS()
    convo = Conversation(stt, brain, tts)

    turn = convo.turn()

    assert turn is None
    assert brain.heard == []
    assert tts.spoken == []


def test_run_loops_until_should_continue_is_false():
    stt = FakeSTT(["one", "two", "three"])
    brain = FakeBrain()
    tts = FakeTTS()
    convo = Conversation(stt, brain, tts, acknowledger=lambda: "ACK")

    checks = {"n": 0}

    def should_continue():
        checks["n"] += 1
        return checks["n"] <= 2

    convo.run(should_continue=should_continue)

    assert brain.heard == ["one", "two"]
    assert tts.spoken == ["ACK", "reply to one", "ACK", "reply to two"]


def test_farewell_ends_the_conversation_without_asking_the_brain():
    stt = FakeSTT(["hi", "Goodbye, Entity.", "unreachable"])
    brain = FakeBrain()
    tts = FakeTTS()
    convo = Conversation(stt, brain, tts)

    convo.run()

    assert brain.heard == ["hi"]
    assert "Goodbye, Entity." not in brain.heard
    assert tts.spoken[-1] == convo.farewell_reply


def test_quit_and_exit_are_also_farewells():
    for word in ["quit", "Exit.", "goodbye entity"]:
        convo = Conversation(FakeSTT([word]), FakeBrain(), FakeTTS())
        assert convo.turn().farewell is True


def test_brain_failure_is_spoken_and_loop_survives():
    class BoomBrain:
        def respond(self, utterance):
            raise RuntimeError("network hiccup")

    stt = FakeSTT(["hello", "goodbye entity"])
    tts = FakeTTS()
    convo = Conversation(stt, BoomBrain(), tts)

    convo.run()

    assert convo.error_reply in tts.spoken
    assert tts.spoken[-1] == convo.farewell_reply


def test_run_reports_each_completed_turn_to_on_turn():
    stt = FakeSTT(["a", "goodbye entity"])
    convo = Conversation(stt, FakeBrain(), FakeTTS())

    seen = []
    convo.run(on_turn=seen.append)

    assert [t.heard for t in seen] == ["a", "goodbye entity"]
    assert seen[-1].farewell is True


def test_suspend_pauses_the_brain_until_resume():
    stt = FakeSTT(["suspend", "what's the weather", "resume", "hi there", "goodbye entity"])
    brain = FakeBrain()
    tts = FakeTTS()
    convo = Conversation(stt, brain, tts)

    convo.run()

    assert brain.heard == ["hi there"]  # nothing reached the brain while paused
    assert convo.suspend_reply in tts.spoken
    assert convo.resume_reply in tts.spoken
