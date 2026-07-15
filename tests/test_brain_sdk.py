from entity.brain_sdk import _extract_text


class FakeBlock:
    def __init__(self, text):
        self.text = text


class FakeMsg:
    def __init__(self, content):
        self.content = content


def test_extract_text_concatenates_text_blocks():
    msgs = [FakeMsg([FakeBlock("Hey "), FakeBlock("the user.")])]

    assert _extract_text(msgs) == "Hey the user."


def test_extract_text_ignores_non_text_blocks():
    class ThinkingBlock:
        thinking = "let me think"

    msgs = [FakeMsg([ThinkingBlock(), FakeBlock("Hi.")])]

    assert _extract_text(msgs) == "Hi."


def test_extract_text_skips_messages_without_content():
    class ResultLike:
        pass

    msgs = [ResultLike(), FakeMsg([FakeBlock("Only this.")])]

    assert _extract_text(msgs) == "Only this."
