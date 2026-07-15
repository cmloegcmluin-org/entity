import json

import pytest

from entity.brain_claude import BrainError, ClaudeBrain


class FakeRun:
    """Stands in for the subprocess call to `claude`; records calls, returns canned JSON."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, cmd, stdin_text, cwd):
        self.calls.append((cmd, stdin_text, cwd))
        return json.dumps(self._responses.pop(0))


def test_respond_returns_claude_result_and_builds_expected_command():
    run = FakeRun([{"result": "Hi the user.", "session_id": "s1", "is_error": False}])
    brain = ClaudeBrain(persona="PERSONA", model="sonnet", run=run)

    said = brain.respond("hello")

    assert said == "Hi the user."
    cmd, stdin_text, cwd = run.calls[0]
    assert stdin_text == "hello"
    assert cmd[:2] == ["claude", "-p"]
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--system-prompt") + 1] == "PERSONA"
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert cmd[cmd.index("--setting-sources") + 1] == "project,local"
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert "--resume" not in cmd


def test_second_turn_resumes_the_captured_session():
    run = FakeRun(
        [
            {"result": "one", "session_id": "sess-1", "is_error": False},
            {"result": "two", "session_id": "sess-1", "is_error": False},
        ]
    )
    brain = ClaudeBrain(persona="P", run=run)

    brain.respond("first")
    brain.respond("second")

    first_cmd, second_cmd = run.calls[0][0], run.calls[1][0]
    assert "--resume" not in first_cmd
    assert second_cmd[second_cmd.index("--resume") + 1] == "sess-1"


def test_error_result_raises_brain_error():
    run = FakeRun([{"result": "rate limited", "is_error": True}])
    brain = ClaudeBrain(persona="P", run=run)

    with pytest.raises(BrainError):
        brain.respond("hi")
