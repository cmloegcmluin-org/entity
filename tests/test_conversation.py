from entity.conversation import Conversation, Turn


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
    convo = Conversation(stt, brain, tts)

    turn = convo.turn()

    assert brain.heard == ["hello"]
    assert tts.spoken == ["reply to hello"]
    assert turn == Turn(heard="hello", said="reply to hello")


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
    convo = Conversation(stt, brain, tts)

    checks = {"n": 0}

    def should_continue():
        checks["n"] += 1
        return checks["n"] <= 2

    convo.run(should_continue=should_continue)

    assert brain.heard == ["one", "two"]
    assert tts.spoken == ["reply to one", "reply to two"]


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
