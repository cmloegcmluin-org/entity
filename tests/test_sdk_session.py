import threading
from pathlib import Path

import pytest
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage

from entity.sdk_session import SdkSession, _context_tokens, extract_text


class FakeBlock:
    def __init__(self, text):
        self.text = text


class FakeMsg:
    def __init__(self, content):
        self.content = content


def test_extract_text_concatenates_text_blocks_within_a_message():
    msgs = [FakeMsg([FakeBlock("Hey "), FakeBlock("the user.")])]

    assert extract_text(msgs) == "Hey the user."


def test_extract_text_keeps_only_the_final_message_not_the_running_narration():
    # a tool-using turn narrates each step; only the last message is the answer the user should hear.
    msgs = [
        FakeMsg([FakeBlock("I'll read the worktree's CLAUDE.md first.")]),
        FakeMsg([FakeBlock("Now let me find where the link is built.")]),
        FakeMsg([FakeBlock("Found it. The agent is on it now, over.")]),
    ]

    assert extract_text(msgs) == "Found it. The agent is on it now, over."


def test_extract_text_ignores_non_text_blocks():
    class ThinkingBlock:
        thinking = "let me think"

    msgs = [FakeMsg([ThinkingBlock(), FakeBlock("Hi.")])]

    assert extract_text(msgs) == "Hi."


def test_extract_text_skips_messages_without_content():
    class ResultLike:
        pass

    msgs = [ResultLike(), FakeMsg([FakeBlock("Only this.")])]

    assert extract_text(msgs) == "Only this."


def test_context_tokens_sums_every_input_side_count():
    # the true size of the context the model just processed = fresh input + both cache tiers;
    # that's what governs how slow the turn was, so it's what we watch to decide on compaction.
    usage = {
        "input_tokens": 2,
        "cache_creation_input_tokens": 576,
        "cache_read_input_tokens": 21319,
        "output_tokens": 4,  # output is NOT context the next turn re-processes
    }
    assert _context_tokens(usage) == 2 + 576 + 21319


def test_context_tokens_is_zero_when_usage_is_missing_or_empty():
    assert _context_tokens(None) == 0
    assert _context_tokens({}) == 0


class FakeClient:
    """A stand-in ClaudeSDKClient whose async methods just record that they ran, so the session's
    threading/loop plumbing can be tested without the real CLI."""

    def __init__(self, *, options=None):
        self.interrupted = threading.Event()
        self.disconnected = threading.Event()

    async def connect(self):
        pass

    async def interrupt(self):
        self.interrupted.set()

    async def disconnect(self):
        self.disconnected.set()


def _finished():
    return ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                         num_turns=1, session_id="s", usage={})


class StreamingClient(FakeClient):
    """A client that streams a turn back: a tool call, its output, then the agent's words."""

    STREAM = (
        FakeMsg([FakeBlock("Confirmed red.")]),
        FakeMsg([FakeBlock("Now the implementation:")]),
        _finished(),
    )

    async def query(self, prompt):
        self.asked = prompt

    async def receive_response(self):
        for message in self.STREAM:
            yield message


def test_every_message_reaches_the_caller_whole_as_it_streams():
    # It used to boil each message down to its text right here, so the tool calls, the diffs and
    # the command output were gone before anything downstream could write them anywhere.
    client = StreamingClient()
    session = SdkSession(ClaudeAgentOptions(), client_factory=lambda options: client)
    seen = []

    reply = session.ask("do the thing", on_message=seen.append)

    assert seen == list(StreamingClient.STREAM)  # every message, untouched
    assert reply == "Now the implementation:"  # the reply is still only its final word
    session.close()


def test_interrupt_runs_the_clients_interrupt_on_the_session_loop():
    client = FakeClient()
    session = SdkSession(ClaudeAgentOptions(), client_factory=lambda options: client)

    session.interrupt()

    assert client.interrupted.is_set()  # the cancel was driven on the session's own event loop
    session.close()


def test_a_closed_session_refuses_work_instead_of_hanging_on_it_forever():
    # `close()` stops this session's private event loop, and a coroutine handed to a stopped loop is
    # queued and never run - so `.result()` waits for something that cannot happen. Anything holding
    # a closed session then stops answering ALTOGETHER rather than failing: no reply, no error, no
    # end to the wait. A brain rebuild that fails leaves exactly that session in place.
    session = SdkSession(ClaudeAgentOptions(), client_factory=lambda options: FakeClient())
    session.close()

    outcome = []
    asking = threading.Thread(target=lambda: outcome.append(_ask_quietly(session)), daemon=True)
    asking.start()
    asking.join(2.0)

    assert not asking.is_alive(), "asking a closed session never came back"
    assert isinstance(outcome[0], Exception)  # it fails, and a failure is something callers can see


def _ask_quietly(session):
    try:
        return session.ask("are you there")
    except Exception as exc:
        return exc


def _capturing_factory(seen):
    def factory(*, options):
        seen.append(options)
        return FakeClient()
    return factory


def test_a_system_prompt_is_handed_over_as_a_file_rather_than_spelled_out():
    # The SDK writes the system prompt out on the CLI's command line, and Windows refuses a command
    # line over 32767 characters - as a FileNotFoundError, which the SDK reports as the CLI being
    # missing. So a persona that outgrew that budget made EVERY session fail to start, saying
    # "Claude Code not found at: ...claude.exe" about a file that was sitting right there. A path
    # is a few dozen characters however long the persona gets.
    persona = "x" * 40000
    seen = []
    session = SdkSession(ClaudeAgentOptions(system_prompt=persona),
                         client_factory=_capturing_factory(seen))

    handed = seen[0].system_prompt
    assert handed["type"] == "file"
    assert Path(handed["path"]).read_text(encoding="utf-8") == persona
    session.close()


def test_the_spilled_system_prompt_is_cleaned_up_when_the_session_ends():
    # The brain builds a fresh session on every compaction and every reconnect, so a file left
    # behind each time is an accumulating pile of copies of the user's own standing profile.
    seen = []
    session = SdkSession(ClaudeAgentOptions(system_prompt="who you are"),
                         client_factory=_capturing_factory(seen))
    spilled = Path(seen[0].system_prompt["path"])

    session.close()

    assert not spilled.exists()


def test_a_session_that_never_connects_takes_its_spilled_prompt_back_with_it():
    # The failure path is the one that repeats: a brain whose session died rebuilds on every turn,
    # and a rebuild that also fails never reaches close(). Thirty-four minutes of that is thirty-four
    # minutes of dropping copies of the user's profile into the temp directory.
    class RefusingClient(FakeClient):
        async def connect(self):
            raise RuntimeError("the CLI would not start")

    seen = []

    def factory(*, options):
        seen.append(options)
        return RefusingClient()

    with pytest.raises(RuntimeError):
        SdkSession(ClaudeAgentOptions(system_prompt="who you are"), client_factory=factory)

    assert not Path(seen[0].system_prompt["path"]).exists()
