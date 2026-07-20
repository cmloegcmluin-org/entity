import threading

from claude_agent_sdk import ResultMessage

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
    session = SdkSession(object(), client_factory=lambda options: client)
    seen = []

    reply = session.ask("do the thing", on_message=seen.append)

    assert seen == list(StreamingClient.STREAM)  # every message, untouched
    assert reply == "Now the implementation:"  # the reply is still only its final word
    session.close()


def test_interrupt_runs_the_clients_interrupt_on_the_session_loop():
    client = FakeClient()
    session = SdkSession(object(), client_factory=lambda options: client)

    session.interrupt()

    assert client.interrupted.is_set()  # the cancel was driven on the session's own event loop
    session.close()
