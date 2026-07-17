import pytest

from entity.brain_sdk import BrainInterrupted, SdkBrain, _is_usage_limit, _make_options

_LIMIT = "You've hit your monthly spend limit - raise it at claude.ai/settings/usage"


def test_is_usage_limit_spots_the_cli_spend_notice():
    assert _is_usage_limit(_LIMIT) is True
    assert _is_usage_limit("You've hit your usage limit.") is True
    assert _is_usage_limit("Merged it - the drive icon opens the folder now.") is False


def test_a_remember_false_turn_stays_out_of_the_recent_window():
    # the heartbeat's silent "any agent news?" polls must not crowd out the real conversation.
    class Session:
        def __init__(self, options):
            self.last_context_tokens = 0

        def ask(self, message):
            return f"reply to {message}"

        def close(self):
            pass

    brain = SdkBrain(session_factory=Session)
    brain.respond("what's the plan for today")
    brain.respond("HEARTBEAT poll", remember=False)

    carried = [utterance for utterance, _ in brain._recent]
    assert "what's the plan for today" in carried
    assert "HEARTBEAT poll" not in carried  # the poll didn't enter the carried-forward memory


def test_respond_rebuilds_the_session_when_it_hits_a_usage_limit_then_recovers():
    made = []

    class LimitedThenBackSession:
        def __init__(self, options):
            made.append(self)
            self.closed = False
            self.last_context_tokens = 0

        def ask(self, message):
            if made.index(self) == 0:  # the wedged session parrots the spend-limit notice
                return _LIMIT
            return "Merged. The drive icon opens the folder now."  # a fresh session, usage back

        def close(self):
            self.closed = True

    brain = SdkBrain(session_factory=LimitedThenBackSession)

    assert brain.respond("merge it") == "Merged. The drive icon opens the folder now."
    assert len(made) == 2 and made[0].closed  # it rebuilt past the wedged session, didn't loop


def test_a_persistent_usage_limit_is_surfaced_once_not_looped_forever():
    made = []

    class StillLimitedSession:
        def __init__(self, options):
            made.append(self)
            self.closed = False
            self.last_context_tokens = 0

        def ask(self, message):
            return _LIMIT  # usage genuinely still gone on every session

        def close(self):
            self.closed = True

    brain = SdkBrain(session_factory=StillLimitedSession)

    assert _is_usage_limit(brain.respond("hi"))  # says it once
    assert len(made) == 2  # exactly one rebuild+retry, not an unbounded loop


def test_interrupt_cancels_the_current_session():
    made = []

    class InterruptibleSession:
        def __init__(self, options):
            made.append(self)
            self.interrupted = False
            self.last_context_tokens = 0

        def ask(self, message):
            return "hi"

        def interrupt(self):
            self.interrupted = True

        def close(self):
            pass

    brain = SdkBrain(session_factory=InterruptibleSession)
    brain.interrupt()

    assert made[0].interrupted is True  # the barge-in was forwarded to the live session


def test_respond_does_not_retry_after_an_interrupt():
    # A barge-in lands mid-ask and the stream aborts. respond must NOT reconnect-and-re-ask
    # (that would re-run the very work we cancelled) - it surfaces the cancellation instead.
    made = []

    class AbortedSession:
        def __init__(self, options):
            made.append(self)
            self.asks = 0
            self.last_context_tokens = 0

        def ask(self, message):
            self.asks += 1
            brain.interrupt()  # he barges in while we're waiting on the model
            raise RuntimeError("stream aborted by interrupt")

        def interrupt(self):
            pass

        def close(self):
            pass

    brain = SdkBrain(session_factory=AbortedSession)

    with pytest.raises(BrainInterrupted):
        brain.respond("a big job")
    assert made[0].asks == 1  # asked once
    assert len(made) == 1  # and did NOT reconnect a fresh session to retry


def test_respond_discards_a_partial_reply_after_an_interrupt():
    # When the interrupt lands, the CLI may still return a half-finished reply. respond must drop
    # it - not speak it, and not seed it into the history carried across a compaction.
    class PartialSession:
        def __init__(self, options):
            self.last_context_tokens = 0

        def ask(self, message):
            brain.interrupt()
            return "half a sentence he never asked to h"

        def interrupt(self):
            pass

        def close(self):
            pass

    brain = SdkBrain(session_factory=PartialSession)

    with pytest.raises(BrainInterrupted):
        brain.respond("x")
    assert list(brain._recent) == []  # the abandoned partial was not remembered


def test_a_fresh_respond_after_an_interrupt_works_normally():
    # The cancel flag from one turn must not gag the next turn.
    class Session:
        def __init__(self, options):
            self.last_context_tokens = 0

        def ask(self, message):
            return f"reply to {message}"

        def interrupt(self):
            pass

        def close(self):
            pass

    brain = SdkBrain(session_factory=Session)
    brain.interrupt()  # a leftover cancel from a previous turn

    assert brain.respond("hello") == "reply to hello"  # the new turn is not cancelled


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
            self.last_context_tokens = 0

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


class GrowingSession:
    """A fake whose context grows by `per_turn` tokens each ask and echoes the message, so a test
    can watch context climb and then check what the reseeded session was handed."""

    def __init__(self, options, *, per_turn=20000):
        self.options = options
        self.asks = []
        self.closed = False
        self.last_context_tokens = 0
        self._per_turn = per_turn

    def ask(self, message):
        self.asks.append(message)
        self.last_context_tokens += self._per_turn
        return f"reply to {message}"

    def close(self):
        self.closed = True


def _growing_factory(sessions, *, per_turn=20000):
    def factory(options):
        s = GrowingSession(options, per_turn=per_turn)
        sessions.append(s)
        return s

    return factory


def test_stays_on_one_session_while_context_stays_small():
    sessions = []
    brain = SdkBrain(session_factory=_growing_factory(sessions, per_turn=1000), compact_growth_budget=30000)
    for _ in range(6):
        brain.respond("hi")

    assert len(sessions) == 1  # 6 small turns never crossed the budget, so no compaction


def test_compacts_onto_a_fresh_session_when_context_grows_past_budget():
    sessions = []
    brain = SdkBrain(
        persona="BASE PERSONA", session_factory=_growing_factory(sessions), compact_growth_budget=30000
    )
    # turn1 -> 20k (baseline). turn2 -> 40k. turn3 -> 60k. turn4 sees 60k-20k=40k >= 30k -> compact.
    replies = [brain.respond(f"q{i}") for i in range(4)]

    assert len(sessions) == 2  # compacted exactly once
    assert sessions[0].closed  # the bloated session was closed
    assert replies[-1] == "reply to q3"  # the caller got its real reply, uninterrupted
    # compaction is a cheap reseed, NOT an expensive summary call - the old session got no extra ask
    assert sessions[0].asks == ["q0", "q1", "q2"]


def test_the_reseeded_session_carries_the_base_persona_plus_the_recent_turns_verbatim():
    sessions = []
    brain = SdkBrain(
        persona="BASE PERSONA", session_factory=_growing_factory(sessions), compact_growth_budget=30000
    )
    for i in range(4):
        brain.respond(f"q{i}")

    seeded = sessions[1].options.system_prompt
    assert seeded.startswith("BASE PERSONA")  # the base persona is preserved, not lost
    # the turns that happened before the reset are carried forward verbatim, so nothing is fabricated
    assert "q0" in seeded and "q2" in seeded
    assert "reply to q1" in seeded


def test_only_the_most_recent_turns_are_carried_across_a_reset():
    sessions = []
    brain = SdkBrain(
        session_factory=_growing_factory(sessions),
        compact_growth_budget=30000,
        recent_turns_kept=2,
    )
    for i in range(4):
        brain.respond(f"q{i}")

    seeded = sessions[1].options.system_prompt
    assert "q2" in seeded  # kept (recent)
    assert "q0" not in seeded  # dropped - only the last 2 turns are carried, bounding the reseed size


def test_a_compacted_session_does_not_immediately_compact_again():
    sessions = []
    brain = SdkBrain(session_factory=_growing_factory(sessions), compact_growth_budget=30000)
    for _ in range(7):  # enough to cross the budget a second time if the baseline reset works
        brain.respond("hi")

    # first epoch: turns 1-3 on session0, compact at turn4 -> session1 re-baselines at 20k;
    # session1 grows 20k,40k,60k over turns 4-6, compact again at turn7 -> session2. Two compactions.
    assert len(sessions) == 3
    assert sessions[1].closed
