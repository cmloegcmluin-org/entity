from entity.brain_sdk import _extract_text, _make_options


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


def test_brain_is_isolated_from_user_settings_and_hooks():
    # The fix for the leak: load NO user/project/local settings, so the Entity never
    # inherits the global coding CLAUDE.md or the Stop hook that enforces the terminal
    # reply format (which otherwise bleeds ">>"/">" blocks in and explodes latency).
    opts = _make_options("PERSONA", "sonnet")

    assert list(opts.setting_sources) == []
    assert list(opts.allowed_tools) == []
    assert opts.system_prompt == "PERSONA"
