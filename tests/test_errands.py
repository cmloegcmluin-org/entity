import time

from entity.errands import ERRAND_MODEL, ErrandRunner


class FakeSession:
    def __init__(self, reply="Moved the log into the archive."):
        self._reply = reply
        self.asked = []

    def ask(self, prompt, on_message=None, on_text=None):
        self.asked.append(prompt)
        return self._reply


def _runner(reply="Moved the log into the archive."):
    session = FakeSession(reply)
    events = []
    made = []

    def factory(options):
        made.append(options)
        return session

    runner = ErrandRunner("C:/runtime", lambda *event: events.append(event),
                          session_factory=factory)
    return runner, session, events, made


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_a_chore_runs_quietly_and_its_outcome_becomes_an_event():
    # "I just don't want my agents log tab to be cluttered with an agent for every little thing" -
    # no desk entry, no tab: one helper session does it and the outcome takes the news road.
    runner, session, events, _ = _runner()

    runner.run("move runtime/agent-logs/old.log into the archive folder")

    assert _wait_for(lambda: bool(events))
    assert "move runtime/agent-logs/old.log" in session.asked[0]
    [(kind, agent, report)] = events
    assert kind == "errand"
    assert report == "Moved the log into the archive."


def test_a_failed_errand_is_news_not_silence():
    class BrokenSession:
        def ask(self, prompt, on_message=None, on_text=None):
            raise RuntimeError("session wedged")

    events = []
    runner = ErrandRunner("C:/runtime", lambda *event: events.append(event),
                          session_factory=lambda options: BrokenSession())

    runner.run("tidy the folder")

    assert _wait_for(lambda: bool(events))
    [(kind, _, report)] = events
    assert kind == "errand"
    assert "could not run" in report


def test_the_errand_hand_is_a_small_model_with_file_tools_and_no_tab():
    runner, session, events, made = _runner()

    runner.run("anything")

    assert _wait_for(lambda: bool(events))
    [options] = made
    assert options.model == ERRAND_MODEL
    assert "Bash" in options.allowed_tools and "Write" in options.allowed_tools
    assert options.cwd == "C:/runtime"


def test_one_helper_session_serves_every_chore():
    runner, session, events, made = _runner()

    runner.run("first chore")
    assert _wait_for(lambda: len(events) == 1)
    runner.run("second chore")
    assert _wait_for(lambda: len(events) == 2)

    assert len(made) == 1
