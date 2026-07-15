from entity.brain_sdk import _make_options


def test_brain_is_isolated_from_user_settings_and_hooks():
    # The fix for the leak: load NO user/project/local settings, so the Entity never
    # inherits the global coding CLAUDE.md or the Stop hook that enforces the terminal
    # reply format (which otherwise bleeds ">>"/">" blocks in and explodes latency).
    opts = _make_options("PERSONA", "sonnet")

    assert list(opts.setting_sources) == []
    assert list(opts.allowed_tools) == []
    assert opts.system_prompt == "PERSONA"
