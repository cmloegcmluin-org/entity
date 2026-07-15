from entity.brain_sdk import SdkBrain, _make_options


def test_brain_is_isolated_from_user_settings_and_hooks():
    # The fix for the leak: load NO user/project/local settings, so the Entity never
    # inherits the global coding CLAUDE.md or the Stop hook that enforces the terminal
    # reply format (which otherwise bleeds ">>"/">" blocks in and explodes latency).
    opts = _make_options("PERSONA", "sonnet")

    assert list(opts.setting_sources) == []
    assert list(opts.allowed_tools) == []
    assert opts.system_prompt == "PERSONA"


def test_respond_rebuilds_a_wedged_session_and_retries_once():
    made = []

    class FlakySession:
        def __init__(self, options):
            made.append(self)
            self.closed = False

        def ask(self, message):
            if made.index(self) == 0:  # the first session is wedged
                raise RuntimeError("connection dropped")
            return "recovered reply"

        def close(self):
            self.closed = True

    brain = SdkBrain(session_factory=FlakySession)

    assert brain.respond("hi") == "recovered reply"
    assert len(made) == 2  # rebuilt the session after the error
    assert made[0].closed  # and closed the wedged one
