from entity.sdk_session import _context_tokens, extract_text


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
