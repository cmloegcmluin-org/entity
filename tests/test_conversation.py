import threading
import time

from entity.console import Console
from entity.conversation import (
    Conversation,
    Turn,
    _default_reassurance,
    _humanize_elapsed,
    _is_affirmative,
)
from entity.outbox import Outbox


class VariableBrain:
    """Replies long to a big ask, short to anything else - so the long-answer gate can be exercised
    without a real (slow, wordy) model."""

    WALL = "This is a very long answer. " * 40  # comfortably past the gate threshold

    def __init__(self):
        self.heard = []

    def respond(self, utterance):
        self.heard.append(utterance)
        return self.WALL if "everything" in utterance else "a short reply"


class FakeSTT:
    def __init__(self, utterances):
        self._utterances = list(utterances)
        self.calls = 0

    def listen(self):
        self.calls += 1
        if not self._utterances:
            raise AssertionError("STT exhausted - the loop failed to stop")
        return self._utterances.pop(0)


class FakeBrain:
    def __init__(self):
        self.heard = []

    def respond(self, utterance):
        self.heard.append(utterance)
        return f"reply to {utterance}"


class FakeTTS:
    def __init__(self):
        self.spoken = []

    def speak(self, text, *, interrupt=None):
        self.spoken.append(text)


def test_a_barge_in_before_the_reply_leaves_it_unspoken():
    interrupt = threading.Event()

    class InterruptingBrain:  # they hit Enter while it's thinking
        def respond(self, utterance):
            interrupt.set()
            return "a fifteen-minute novella they never wanted"

    tts = FakeTTS()
    convo = Conversation(FakeSTT(["hi"]), InterruptingBrain(), tts, acknowledgement="ACK", interrupt=interrupt)
    convo.turn()

    assert tts.spoken == ["ACK"]  # the ack got out before the cut; the reply is silenced


def test_the_reply_is_printed_to_the_terminal_before_it_is_spoken():
    events = []

    class RecordingTTS:
        def speak(self, text, *, interrupt=None):
            events.append(f"say:{text}")

    console = Console(echo=lambda line: events.append(f"print:{line}"))
    convo = Conversation(FakeSTT(["hi"]), FakeBrain(), RecordingTTS(), acknowledgement="ACK", console=console)

    convo.turn()

    printed = next(e for e in events if e.startswith("print:entity>") and "reply to hi" in e)
    assert events.index(printed) < events.index("say:reply to hi")  # they can read it before/while it speaks


def test_timings_prints_a_think_and_speak_readout_when_enabled():
    lines = []
    convo = Conversation(
        FakeSTT(["hi"]), FakeBrain(), FakeTTS(),
        acknowledgement="ACK", timings=True, console=Console(echo=lines.append),
    )

    convo.turn()

    assert any("· speak" in line for line in lines)  # the per-turn think/speak readout showed


def test_no_timings_readout_when_disabled():
    lines = []
    convo = Conversation(
        FakeSTT(["hi"]), FakeBrain(), FakeTTS(),
        acknowledgement="ACK", timings=False, console=Console(echo=lines.append),
    )

    convo.turn()

    assert not any("· speak" in line for line in lines)


def test_a_thinking_indicator_is_shown_while_it_thinks():
    shown = []
    console = Console(echo=shown.append)
    convo = Conversation(FakeSTT(["hi"]), FakeBrain(), FakeTTS(), acknowledgement="ACK", console=console)

    convo.turn()

    assert any("thinking" in line.lower() for line in shown)


def test_it_pauses_after_a_reply_to_give_a_beat_to_read():
    slept = []
    convo = Conversation(
        FakeSTT(["hi"]), FakeBrain(), FakeTTS(),
        acknowledgement="ACK", read_pause=1.5, sleep=slept.append,
    )

    convo.turn()

    assert slept == [1.5]  # a beat to read the reply before listening starts again


def test_no_read_pause_after_a_control_phrase():
    slept = []
    convo = Conversation(FakeSTT(["suspend"]), FakeBrain(), FakeTTS(), read_pause=1.5, sleep=slept.append)

    convo.turn()

    assert slept == []  # nothing substantive to read, so no beat


def test_read_pause_is_skipped_when_he_barges_in():
    slept = []
    interrupt = threading.Event()

    class InterruptingBrain:
        def respond(self, utterance):
            interrupt.set()  # they cut in as the reply lands
            return "reply"

    convo = Conversation(
        FakeSTT(["hi"]), InterruptingBrain(), FakeTTS(),
        acknowledgement="ACK", read_pause=1.5, sleep=slept.append, interrupt=interrupt,
    )

    convo.turn()

    assert slept == []  # they're cutting in - don't make them wait out a read pause


def test_is_affirmative_reads_a_yes_but_not_a_no():
    assert _is_affirmative("yes") and _is_affirmative("yeah go ahead") and _is_affirmative("sure, hit me")
    assert _is_affirmative("okay") and _is_affirmative("let's hear it")
    assert not _is_affirmative("no thanks") and not _is_affirmative("not now") and not _is_affirmative("nope")
    assert not _is_affirmative("maybe later")  # "later" is a decline, not a yes
    assert not _is_affirmative("don't go ahead")  # a negated yes is that negation, not a yes
    assert _is_affirmative("it's not great, but yes")  # a real yes beside an unrelated negative


def test_a_short_answer_is_spoken_immediately_not_gated():
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["hi"]), FakeBrain(), tts, acknowledgement="ACK", long_answer_chars=100)

    turn = convo.turn()

    assert tts.spoken == ["ACK", "reply to hi"]  # short reply: no "ready?" gate
    assert turn.said == "reply to hi"


def test_a_long_answer_is_offered_first_then_delivered_on_a_yes():
    brain = VariableBrain()
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["tell me everything", "yes"]), brain, tts,
                         acknowledgement="ACK", long_answer_chars=100)

    first = convo.turn()

    assert first.said == convo.ready_question  # it asked instead of dumping the wall of text
    assert not any(line.startswith("This is a very long answer.") for line in tts.spoken)  # nothing dumped yet

    convo.turn()  # they say "yes"

    assert any(line.startswith("This is a very long answer.") for line in tts.spoken)  # the yes released the full answer


def test_a_declined_long_answer_is_dropped_and_the_new_utterance_is_handled():
    brain = VariableBrain()
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["tell me everything", "no thanks"]), brain, tts,
                         acknowledgement="ACK", long_answer_chars=100)

    convo.turn()  # offers "ready?"
    convo.turn()  # "no thanks"

    assert not any(line.startswith("This is a very long answer.") for line in tts.spoken)  # they declined, so it was never delivered
    assert "a short reply" in tts.spoken  # and "no thanks" was handled as an ordinary turn
    assert brain.heard == ["tell me everything", "no thanks"]


def test_a_yes_delivers_the_offer_even_with_tv_negatives_in_the_same_turn():
    # From the real session: their "yes, I am ready for the longer answer" arrived in the same turn
    # as TV chatter ("I'm not super far..."), and the "not" silently vetoed their yes - the answer they
    # was promised evaporated. A yes anywhere in the turn outranks stray negatives.
    brain = VariableBrain()
    tts = FakeTTS()
    convo = Conversation(
        FakeSTT(["tell me everything",
                 "I'm not super far and not having a great time. Uh, yes, I am ready for the longer answer"]),
        brain, tts, acknowledgement="ACK", long_answer_chars=100,
    )

    convo.turn()  # offers "ready?"
    convo.turn()  # TV garbage + their real yes

    assert any(line.startswith("This is a very long answer.") for line in tts.spoken)  # their yes won; the answer was finally delivered


def test_speech_that_answers_neither_way_leaves_the_offer_standing():
    # TV dialogue with no yes and no no used to silently destroy the offer before they could answer.
    # If it isn't an answer, it isn't an answer - the offer waits for one.
    brain = VariableBrain()
    tts = FakeTTS()
    convo = Conversation(
        FakeSTT(["tell me everything", "press R to unleash a brief burst of teeth", "okay"]),
        brain, tts, acknowledgement="ACK", long_answer_chars=100,
    )

    convo.turn()  # offers "ready?"
    convo.turn()  # TV garbage: neither yes nor no - handled as its own turn, offer kept
    convo.turn()  # their real yes, one turn later

    assert "a short reply" in tts.spoken  # the garbage still got an ordinary answer
    assert any(line.startswith("This is a very long answer.") for line in tts.spoken)  # and their okay still released the held answer


def test_gating_off_speaks_even_a_long_answer_straight_away():
    brain = VariableBrain()
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["tell me everything"]), brain, tts,
                         acknowledgement="ACK", long_answer_chars=None)

    turn = convo.turn()

    assert turn.said == VariableBrain.WALL  # gate disabled -> spoken as before
    assert any(line.startswith("This is a very long answer.") for line in tts.spoken)


def test_a_slow_think_detaches_to_the_background_and_frees_the_loop():
    release = threading.Event()

    class SlowBrain:
        def respond(self, utterance):
            release.wait(2.0)  # never lands before the detach window
            return "the finished long-running answer"

    tts = FakeTTS()
    convo = Conversation(
        FakeSTT(["do the slow thing", ""]), SlowBrain(), tts,
        acknowledgement="ACK", detach_after=0.05, patience=30,
    )

    first = convo.turn()

    assert first is None  # it didn't block - the loop is free to listen again
    assert any(line in tts.spoken for line in convo.detach_replies)  # told it's running in the background

    bg_done = convo._background["done"]
    release.set()
    assert bg_done.wait(2.0)  # the background call finishes on its own thread

    convo.turn()  # the lull it woke: the answer goes out on its own

    assert "the finished long-running answer" in tts.spoken  # the promise is kept without being chased
    assert convo.ready_question not in tts.spoken  # and there was nothing for them to say yes to


def test_talking_again_cancels_the_detached_call_instead_of_deflecting_the_user():
    # They were bounced with a canned "still finishing your last one" every time they spoke, which threw
    # their words away and locked them out of the conversation. Their live turn outranks the stale call.
    release = threading.Event()
    calls = []
    cancelled = []

    class SlowBrain:
        def respond(self, utterance):
            calls.append(utterance)
            if len(calls) == 1:
                release.wait(2.0)  # the first call hangs until it's cancelled
                return "stale"
            return "fresh answer"

        def interrupt(self):
            cancelled.append(True)
            release.set()  # the real brain's interrupt frees the one session the same way

    convo = Conversation(
        FakeSTT(["slow one", "what about this"]), SlowBrain(), FakeTTS(),
        acknowledgement="ACK", detach_after=0.05, patience=30,
    )

    convo.turn()  # detaches
    second = convo.turn()  # they speak again while it's still running

    assert cancelled == [True]  # the stale call was cancelled, not left to block them
    assert second.said == "fresh answer"  # and their new turn actually got answered
    assert calls == ["slow one", "what about this"]


def test_a_finished_background_answer_is_spoken_when_they_speak_again():
    # The worst half-hour this program has had. They asked, heard "I'll get back to you on that",
    # waited, and asked again - and asking is what destroyed the answer, because a call that had
    # already FINISHED was cancelled and thrown away as though it were still running. Every push for
    # the answer made it less likely, never more, so pressing harder looked exactly like being
    # ignored. An answer that exists is never something to cancel.
    release = threading.Event()
    holder = {}

    class SlowBrain:
        def __init__(self):
            self.cancelled = False

        def respond(self, utterance):
            if utterance == "how is it going":
                release.wait(2.0)
                return "the answer they were promised"
            return "reply to their new turn"

        def interrupt(self):
            self.cancelled = True

    class TalkingWhileItLands(FakeSTT):
        """It finishes WHILE they are mid-sentence, so the top-of-turn collect has already been and
        gone by the time their words arrive - which is the ordinary case, not a corner one."""

        def listen(self):
            heard = super().listen()
            if heard == "you are taking too long":
                release.set()
                assert holder["convo"]._background["done"].wait(2.0)
            return heard

    brain = SlowBrain()
    tts = FakeTTS()
    convo = Conversation(
        TalkingWhileItLands(["how is it going", "you are taking too long"]), brain, tts,
        acknowledgement="ACK", detach_after=0.05, patience=30, long_answer_chars=None,
    )
    holder["convo"] = convo

    convo.turn()  # detaches: "I'll get back to you on that."
    convo.turn()  # they push for it, and it lands while they are saying so

    assert "the answer they were promised" in tts.spoken  # the promise is kept, not collected
    assert brain.cancelled is False  # nothing to cancel: it had already answered


def test_a_dropped_call_is_shown_so_the_promise_does_not_vanish_silently():
    # They were told "I'll let you know when it's ready" and then never heard back, because their next
    # words quietly killed the call. Whatever else happens, the record shows it was dropped.
    lines = []
    release = threading.Event()

    class SlowBrain:
        # Hangs until cancelled, rather than racing a sleep against the turn: only a call that is
        # genuinely still running is dropped now, so a fixture that might have finished first would
        # be testing whichever branch it happened to land in.
        def respond(self, utterance):
            if utterance == "slow one":
                release.wait(2.0)
            return "answer"

        def interrupt(self):
            release.set()

    convo = Conversation(
        FakeSTT(["slow one", "what about this"]), SlowBrain(), FakeTTS(),
        acknowledgement="ACK", detach_after=0.05, patience=30, cancel_wait=0.1,
        console=Console(echo=lines.append, overwrite=lines.append),
    )

    convo.turn()  # detaches
    convo.turn()  # they speak again - the detached call is dropped for them

    assert any("dropped" in line for line in lines)


def test_every_long_wait_says_the_one_line_he_asked_for():
    # They asked for this sentence literally; the flowery variations were worse than repetition.
    class SlowBrain:
        def respond(self, utterance):
            time.sleep(0.2)
            return "answer"

        def interrupt(self):
            pass

    tts = FakeTTS()
    convo = Conversation(
        FakeSTT(["one", "two", "three"]), SlowBrain(), tts,
        acknowledgement="ACK", detach_after=0.05, patience=30, cancel_wait=0.1,
    )

    for _ in range(3):
        convo.turn()

    said = [line for line in tts.spoken if line in convo.detach_replies]
    assert said == ["I'll get back to you on that."] * 3

def test_a_finished_background_answer_wakes_a_lull():
    wake = threading.Event()
    release = threading.Event()

    class SlowBrain:
        def respond(self, utterance):
            release.wait(2.0)
            return "bg answer"

    convo = Conversation(
        FakeSTT(["slow"]), SlowBrain(), FakeTTS(),
        detach_after=0.05, patience=30, wake=wake,
    )

    convo.turn()  # detaches, arms the reaper

    assert not wake.is_set()
    release.set()
    assert wake.wait(2.0)  # when the answer lands, the lull is broken so it can be offered promptly


def test_a_collected_answer_is_gated_on_its_length_like_any_other_reply():
    # "That wasn't a very long message. Why did you say you had a longer answer for me?" - the
    # background path announced EVERY answer it collected, however short, so a forty-character reply
    # arrived as "I've got a longer answer for you - ready for it?" and then sat unclaimed for
    # twenty-one minutes before he said yes to it. A collected answer is a reply like any other.
    def collected(answer, **settings):
        release = threading.Event()

        class SlowBrain:
            def respond(self, utterance):
                release.wait(2.0)
                return answer

        tts = FakeTTS()
        convo = Conversation(FakeSTT(["slow", ""]), SlowBrain(), tts, acknowledgement="ACK",
                             detach_after=0.05, patience=30, **settings)
        convo.turn()  # detaches
        release.set()
        assert convo._background["done"].wait(2.0)
        convo.turn()  # collects
        return convo, tts

    convo, tts = collected("Started 1 agent.", long_answer_chars=100)

    assert "Started 1 agent." in tts.spoken  # short enough to just say, so it is just said
    assert convo.ready_question not in tts.spoken

    convo, tts = collected("A wall of an answer. " * 20, long_answer_chars=100)

    assert convo.ready_question in tts.spoken  # a genuinely big one is still offered first
    assert convo._offered is not None


def test_a_background_error_is_dropped_not_offered():
    release = threading.Event()

    class BoomBrain:
        def respond(self, utterance):
            release.wait(2.0)
            raise RuntimeError("background boom")

    tts = FakeTTS()
    convo = Conversation(
        FakeSTT(["slow", "hi"]), BoomBrain(), tts,
        acknowledgement="ACK", detach_after=0.05, patience=30,
    )

    convo.turn()  # detaches
    bg_done = convo._background["done"]
    release.set()
    assert bg_done.wait(2.0)
    convo.turn()  # collects the failed background call

    assert convo.ready_question not in tts.spoken  # a failed background call is dropped, never offered
    assert convo._offered is None


def test_a_barge_in_while_thinking_cancels_the_brain_and_returns_to_listening():
    interrupt = threading.Event()
    thinking = threading.Event()
    release = threading.Event()

    class SlowInterruptibleBrain:
        def __init__(self):
            self.interrupted = False

        def respond(self, utterance):
            thinking.set()  # we're now inside the brain call
            release.wait(2.0)  # block until cancelled (safety timeout so a bug can't hang the suite)
            return "an essay they never wanted to sit through"

        def interrupt(self):
            self.interrupted = True
            release.set()  # cancelling unblocks the call, as the real SDK interrupt does

    brain = SlowInterruptibleBrain()
    tts = FakeTTS()

    def barge():
        thinking.wait(2.0)
        interrupt.set()  # they hit Enter / says "stop" while it's still thinking

    threading.Thread(target=barge, daemon=True).start()
    convo = Conversation(
        FakeSTT(["do the big thing"]), brain, tts,
        acknowledgement="ACK", interrupt=interrupt, patience=30,
    )
    turn = convo.turn()

    assert turn is None  # the turn was abandoned - the loop is free to listen again
    assert brain.interrupted is True  # the brain was told to drop the in-flight call
    assert "an essay they never wanted to sit through" not in tts.spoken  # cancelled reply stayed unsaid
    assert tts.spoken == ["ACK"]  # only the heard-you ack made it out


def test_a_barge_in_while_thinking_does_not_start_the_next_brain_call_until_the_last_unwinds():
    # The cancel must WAIT for the cancelled call to finish unwinding, so the loop never runs two
    # overlapping brain calls on the one session.
    interrupt = threading.Event()
    thinking = threading.Event()
    release = threading.Event()
    order = []

    class SlowInterruptibleBrain:
        def respond(self, utterance):
            thinking.set()
            release.wait(2.0)
            order.append("unwound")
            return "late reply"

        def interrupt(self):
            release.set()

    brain = SlowInterruptibleBrain()

    def barge():
        thinking.wait(2.0)
        interrupt.set()

    threading.Thread(target=barge, daemon=True).start()
    convo = Conversation(FakeSTT(["go"]), brain, FakeTTS(), interrupt=interrupt, patience=30)
    convo.turn()
    order.append("turn_returned")

    assert order == ["unwound", "turn_returned"]  # the worker finished before turn() handed control back


def test_a_spoken_stop_word_while_thinking_cancels_the_brain():
    interrupt = threading.Event()
    thinking = threading.Event()
    release = threading.Event()

    class SlowInterruptibleBrain:
        def __init__(self):
            self.interrupted = False

        def respond(self, utterance):
            thinking.set()
            release.wait(2.0)
            return "a monologue they tried to stop"

        def interrupt(self):
            self.interrupted = True
            release.set()

    class SpokenStopSTT:
        def listen(self):
            return "do the big thing"

        def catch_stop(self, active):
            # honour the active window like the real mic; report a spoken "stop" once it's thinking
            while active():
                if thinking.is_set():
                    return True
                time.sleep(0.005)
            return False

    brain = SlowInterruptibleBrain()
    tts = FakeTTS()
    convo = Conversation(
        SpokenStopSTT(), brain, tts,
        acknowledgement="ACK", interrupt=interrupt, patience=30,
    )
    turn = convo.turn()

    assert turn is None
    assert brain.interrupted is True  # a spoken "stop" mid-think cancelled it, not only the Enter key
    assert "a monologue they tried to stop" not in tts.spoken


def test_check_ins_still_fire_while_a_stop_watcher_holds_the_mic():
    # A slow think runs a stop-watcher on the mic; a spoken check-in must not need its own second
    # watcher (two readers corrupt one mic) yet must still be spoken.
    class QuietMicSTT:
        def listen(self):
            return "go"

        def catch_stop(self, active):
            while active():
                time.sleep(0.005)
            return False  # they never say stop

    class SlowBrain:
        def respond(self, utterance):
            time.sleep(0.15)
            return "done"

    tts = FakeTTS()
    convo = Conversation(
        QuietMicSTT(), SlowBrain(), tts,
        acknowledgement="ACK", reassurer=lambda seconds: "WAIT",
        patience=0.02, check_in=0.02, interrupt=threading.Event(),
    )
    convo.turn()

    assert "WAIT" in tts.spoken and tts.spoken[-1] == "done"  # check-ins survive the held mic


def test_a_stale_interrupt_is_cleared_at_the_start_of_a_turn():
    interrupt = threading.Event()
    interrupt.set()  # left over from cutting off the previous turn
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["hi"]), FakeBrain(), tts, acknowledgement="ACK", interrupt=interrupt)

    convo.turn()

    assert tts.spoken == ["ACK", "reply to hi"]  # the stale flag didn't gag this fresh turn


def test_the_interrupt_is_forwarded_to_the_tts_so_a_reply_in_progress_can_be_killed():
    interrupt = threading.Event()
    passed = []

    class CapturingTTS:
        def speak(self, text, *, interrupt=None):
            passed.append(interrupt)

    convo = Conversation(FakeSTT(["hi"]), FakeBrain(), CapturingTTS(), interrupt=interrupt)
    convo.turn()

    assert interrupt in passed  # the reply's speak got the interrupt, so a keypress can cut it mid-word


def test_a_spoken_stop_word_cuts_the_voice_off():
    interrupt = threading.Event()

    class StopHearingSTT:  # they say "stop" the instant it starts talking
        def listen(self):
            return "tell me a long story"

        def catch_stop(self, active):
            return True

    convo = Conversation(StopHearingSTT(), FakeBrain(), FakeTTS(), acknowledgement="ACK", interrupt=interrupt)
    convo.turn()

    assert interrupt.is_set()  # the spoken "stop" tripped the same interrupt the Enter key does


def test_stop_listening_sleeps_the_entity_and_hey_entity_wakes_it():
    brain = FakeBrain()
    tts = FakeTTS()
    stt = FakeSTT(["stop listening", "are you there", "hey Entity", "hi again", "goodbye entity"])
    convo = Conversation(stt, brain, tts)

    convo.run()

    assert brain.heard == ["hi again"]  # nothing reached the brain while it was asleep
    assert convo.suspend_reply in tts.spoken and convo.resume_reply in tts.spoken


def test_stop_listening_does_not_quit():
    convo = Conversation(FakeSTT(["stop listening"]), FakeBrain(), FakeTTS())

    turn = convo.turn()

    assert turn.farewell is False  # it sleeps, it doesn't end the conversation


def test_a_command_with_a_stray_word_in_front_still_fires():
    # transcription tacks words on ("okay, stop listening"); exact-match used to miss that
    convo = Conversation(FakeSTT(["okay stop listening"]), FakeBrain(), FakeTTS())
    assert convo.turn().said == convo.suspend_reply


def test_a_trailing_farewell_still_ends_the_conversation():
    convo = Conversation(FakeSTT(["alright well goodbye entity"]), FakeBrain(), FakeTTS())
    assert convo.turn().farewell is True


def test_a_plain_sentence_is_not_mistaken_for_a_command():
    convo = Conversation(FakeSTT(["tell me about the weather"]), FakeBrain(), FakeTTS(), acknowledgement="ACK")
    turn = convo.turn()
    assert turn.farewell is False and turn.said == "reply to tell me about the weather"


def test_the_check_in_says_processing_your_request_not_working_on_it():
    assert "processing your request" in _default_reassurance(30)
    assert "working on it" not in _default_reassurance(30)


def test_turn_transcribes_thinks_and_speaks():
    stt = FakeSTT(["hello"])
    brain = FakeBrain()
    tts = FakeTTS()
    convo = Conversation(stt, brain, tts, acknowledgement="ACK")

    turn = convo.turn()

    assert brain.heard == ["hello"]
    assert tts.spoken == ["ACK", "reply to hello"]  # heard-you beat first, then the real reply
    assert turn == Turn(heard="hello", said="reply to hello")


def test_a_real_turn_acknowledges_the_instant_it_hears_you_before_thinking():
    events = []

    class WatchfulBrain:
        def respond(self, utterance):
            events.append("think")
            return "reply"

    class WatchfulTTS:
        def speak(self, text, *, interrupt=None):
            events.append(f"say:{text}")

    convo = Conversation(FakeSTT(["hi"]), WatchfulBrain(), WatchfulTTS(), acknowledgement="mm-hm")
    convo.turn()

    # the acknowledgement is spoken BEFORE the brain is even asked, so there's no dead air
    assert events == ["say:mm-hm", "think", "say:reply"]


def test_no_acknowledgement_for_blank_farewell_or_suspend():
    for utterance in ["   ", "goodbye entity", "suspend"]:
        tts = FakeTTS()
        convo = Conversation(FakeSTT([utterance]), FakeBrain(), tts, acknowledgement="ACK")
        convo.turn()
        assert "ACK" not in tts.spoken  # nothing to think about, so nothing to acknowledge


def test_queued_agent_news_is_spoken_when_it_is_the_entitys_turn():
    outbox = Outbox()
    outbox.push("Heads up - the auth agent is ready for your review.")
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["goodbye entity"]), FakeBrain(), tts, outbox=outbox)

    convo.turn()

    assert tts.spoken[0] == "Heads up - the auth agent is ready for your review."  # before we listened


def test_an_unprompted_message_is_printed_to_the_terminal_not_only_spoken(capsys):
    outbox = Outbox()
    outbox.push("the deploy agent needs your call")
    convo = Conversation(FakeSTT(["goodbye entity"]), FakeBrain(), FakeTTS(), outbox=outbox)

    convo.turn()

    assert "the deploy agent needs your call" in capsys.readouterr().out  # visible, not just audio


def test_several_queued_messages_are_all_delivered_in_order():
    outbox = Outbox()
    outbox.push("first")
    outbox.push("second")
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["goodbye entity"]), FakeBrain(), tts, outbox=outbox)

    convo.turn()

    assert tts.spoken[0].index("first") < tts.spoken[0].index("second")  # in order, in one breath


def test_without_an_outbox_the_loop_is_unchanged():
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["hi"]), FakeBrain(), tts, acknowledgement="ACK")

    convo.turn()

    assert tts.spoken == ["ACK", "reply to hi"]  # outbox=None interposes nothing


def test_a_message_arriving_during_a_lull_is_spoken_on_the_next_pass():
    outbox = Outbox()

    class LullSTT:
        # the real MicSTT yields "" when its interrupt fires during a lull; mimic that here by
        # having a message land mid-lull and the listen break off empty.
        def __init__(self):
            self.n = 0

        def listen(self):
            self.n += 1
            if self.n == 1:
                outbox.push("the deploy agent hit an error")
                return ""
            return "goodbye entity"

    tts = FakeTTS()
    convo = Conversation(LullSTT(), FakeBrain(), tts, outbox=outbox)
    convo.run()

    assert tts.spoken == ["the deploy agent hit an error", convo.farewell_reply]


def test_the_default_acknowledgement_is_the_plain_line_he_asked_for():
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["hi"]), FakeBrain(), tts)  # no override -> the default

    convo.turn()

    assert tts.spoken[0] == "Message received."  # not "Mm-hm." et al, which read aloud as "m m"


def test_a_slow_reply_checks_in_so_it_does_not_read_as_a_crash():
    class SlowBrain:
        def respond(self, utterance):
            time.sleep(0.15)  # longer than patience, shorter than the 30s recheck default
            return f"reply to {utterance}"

    tts = FakeTTS()
    convo = Conversation(
        FakeSTT(["hello"]), SlowBrain(), tts,
        acknowledgement="ACK", reassurer=lambda seconds: "WAIT", patience=0.02,
    )
    convo.turn()

    assert tts.spoken == ["ACK", "WAIT", "reply to hello"]  # heard-you, one check-in, then the reply


def test_a_long_think_keeps_checking_in_again_and_again():
    class VerySlowBrain:
        def respond(self, utterance):
            time.sleep(0.3)  # many recheck intervals long
            return "done"

    tts = FakeTTS()
    convo = Conversation(
        FakeSTT(["hello"]), VerySlowBrain(), tts,
        acknowledgement="ACK", reassurer=lambda seconds: "WAIT", patience=0.02, check_in=0.02,
    )
    convo.turn()

    assert tts.spoken.count("WAIT") >= 2  # it keeps reassuring, not just once
    assert tts.spoken[0] == "ACK" and tts.spoken[-1] == "done"


def test_the_check_in_reports_how_long_it_has_been():
    assert _humanize_elapsed(6) == "about 5 seconds"
    assert _humanize_elapsed(33) == "about 35 seconds"
    assert _humanize_elapsed(60) == "about 1 minute"
    assert _humanize_elapsed(150) == "about 2 minutes and 30 seconds"
    assert "40 seconds" in _default_reassurance(41)  # the spoken line carries the elapsed time


def test_a_quick_reply_gets_no_check_in():
    tts = FakeTTS()
    convo = Conversation(
        FakeSTT(["hello"]), FakeBrain(), tts,
        acknowledgement="ACK", reassurer=lambda seconds: "WAIT", patience=30,
    )
    convo.turn()

    assert tts.spoken == ["ACK", "reply to hello"]  # fast enough that "WAIT" never fires


def test_a_slow_brain_failure_still_surfaces_as_the_error_reply():
    class SlowBoom:
        def respond(self, utterance):
            time.sleep(0.05)
            raise RuntimeError("hiccup after a pause")

    tts = FakeTTS()
    convo = Conversation(
        FakeSTT(["hello"]), SlowBoom(), tts,
        acknowledgement="ACK", reassurer=lambda seconds: "WAIT", patience=0.01,
    )
    turn = convo.turn()

    assert turn.error is True
    assert tts.spoken[0] == "ACK" and tts.spoken[-1] == convo.error_reply  # off-thread error re-raised


class TerminatedEmptySTT:
    """Reports, per listen(), whether the (possibly empty) turn ended on the terminator - like MicSTT."""

    def __init__(self, results):
        self._results = list(results)  # (text, caught_terminator) per call
        self.caught_terminator = False

    def listen(self):
        text, self.caught_terminator = self._results.pop(0)
        return text


def test_a_bare_over_gets_a_brief_ack_so_he_knows_it_registered():
    # they said only "over"; the turn is empty but the terminator registered, so acknowledge it out
    # loud instead of ignoring them - otherwise they just repeat "over" wondering if they were heard.
    tts = FakeTTS()
    convo = Conversation(TerminatedEmptySTT([("", True)]), FakeBrain(), tts)

    result = convo.turn()

    assert result is None  # nothing to think about, so no brain call and no real turn
    assert tts.spoken == [convo.empty_turn_reply]


def test_an_empty_turn_without_a_terminator_stays_silent():
    # a lull yield (queued agent news) also returns "" - but no terminator was caught, so no ack.
    tts = FakeTTS()
    convo = Conversation(TerminatedEmptySTT([("", False)]), FakeBrain(), tts)

    assert convo.turn() is None
    assert tts.spoken == []


def test_a_blank_line_from_an_stt_without_the_flag_stays_silent():
    # ConsoleSTT has no caught_terminator; a blank typed line must not trigger the spoken ack.
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["   "]), FakeBrain(), tts)

    assert convo.turn() is None
    assert tts.spoken == []


def test_blank_utterance_is_skipped():
    stt = FakeSTT(["   "])
    brain = FakeBrain()
    tts = FakeTTS()
    convo = Conversation(stt, brain, tts)

    turn = convo.turn()

    assert turn is None
    assert brain.heard == []
    assert tts.spoken == []


def test_run_loops_until_should_continue_is_false():
    stt = FakeSTT(["one", "two", "three"])
    brain = FakeBrain()
    tts = FakeTTS()
    convo = Conversation(stt, brain, tts, acknowledgement="ACK")

    checks = {"n": 0}

    def should_continue():
        checks["n"] += 1
        return checks["n"] <= 2

    convo.run(should_continue=should_continue)

    assert brain.heard == ["one", "two"]
    assert tts.spoken == ["ACK", "reply to one", "ACK", "reply to two"]


def test_farewell_ends_the_conversation_without_asking_the_brain():
    stt = FakeSTT(["hi", "Goodbye, Entity.", "unreachable"])
    brain = FakeBrain()
    tts = FakeTTS()
    convo = Conversation(stt, brain, tts)

    convo.run()

    assert brain.heard == ["hi"]
    assert "Goodbye, Entity." not in brain.heard
    assert tts.spoken[-1] == convo.farewell_reply


def test_quit_and_exit_are_also_farewells():
    for word in ["quit", "Exit.", "goodbye entity"]:
        convo = Conversation(FakeSTT([word]), FakeBrain(), FakeTTS())
        assert convo.turn().farewell is True


def test_brain_failure_is_spoken_and_loop_survives():
    class BoomBrain:
        def respond(self, utterance):
            raise RuntimeError("network hiccup")

    stt = FakeSTT(["hello", "goodbye entity"])
    tts = FakeTTS()
    convo = Conversation(stt, BoomBrain(), tts)

    convo.run()

    assert convo.error_reply in tts.spoken
    assert tts.spoken[-1] == convo.farewell_reply


def test_run_reports_each_completed_turn_to_on_turn():
    stt = FakeSTT(["a", "goodbye entity"])
    convo = Conversation(stt, FakeBrain(), FakeTTS())

    seen = []
    convo.run(on_turn=seen.append)

    assert [t.heard for t in seen] == ["a", "goodbye entity"]
    assert seen[-1].farewell is True


def test_suspend_pauses_the_brain_until_resume():
    stt = FakeSTT(["suspend", "what's the weather", "resume", "hi there", "goodbye entity"])
    brain = FakeBrain()
    tts = FakeTTS()
    convo = Conversation(stt, brain, tts)

    convo.run()

    assert brain.heard == ["hi there"]  # nothing reached the brain while paused
    assert convo.suspend_reply in tts.spoken
    assert convo.resume_reply in tts.spoken


def test_a_wake_word_with_words_after_it_still_wakes_it():
    # "Hey Entity. Can you hear me?" ENDS on "hear me", so an ends-with check ignored them and they had
    # to keep repeating themselves until they said the bare phrase alone.
    brain = FakeBrain()
    stt = FakeSTT(["stop listening", "hey Entity, can you hear me?", "so about that bug", "goodbye entity"])
    convo = Conversation(stt, brain, FakeTTS())

    convo.run()

    assert brain.heard == ["so about that bug"]  # it woke on the first try and took the next turn


def test_agent_news_still_arrives_while_it_is_asleep():
    # They asked outright: if they say "stop listening" and then it has something to relay from an
    # agent, does it speak up or wait for them? Sleep silences the user's turns, not the agents' news.
    outbox = Outbox()
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["stop listening", "anything?", "goodbye entity"]),
                         FakeBrain(), tts, outbox=outbox)

    convo.turn()  # "stop listening" - now asleep
    outbox.push("the auth agent is blocked on you")
    convo.turn()  # their words are ignored while asleep, but the news is not

    assert "the auth agent is blocked on you" in tts.spoken


def test_what_it_hears_while_asleep_is_counted_not_transcribed_back():
    # Asleep it still transcribes, but only to catch the wake word. Echoing a TV's dialogue back at
    # them all evening is noise; a collapsing count says "heard you, ignoring you" without the scroll.
    lines = []
    console = Console(echo=lines.append, overwrite=lines.append)
    convo = Conversation(FakeSTT(["stop listening", "some TV dialogue", "more TV dialogue", "goodbye entity"]),
                         FakeBrain(), FakeTTS(), console=console)

    convo.turn()  # "stop listening" - now asleep
    convo.turn()
    convo.turn()

    assert not any("TV dialogue" in line for line in lines)  # never echoed back at them
    assert lines[-1] == "\r(ignoring… 2x)"  # just a tally that ticks up in place


def test_it_says_it_is_listening_before_it_listens():
    lines = []
    console = Console(echo=lines.append, overwrite=lines.append)
    convo = Conversation(FakeSTT(["hi"]), FakeBrain(), FakeTTS(), console=console)

    convo.turn()

    assert lines[0].startswith("(listening")  # before anything they said could be echoed


def test_it_does_not_claim_to_be_listening_while_it_is_asleep():
    # "(listening…)" while asleep is a flat lie - it's transcribing to catch the wake word and
    # throwing the rest away.
    lines = []
    console = Console(echo=lines.append, overwrite=lines.append)
    convo = Conversation(FakeSTT(["stop listening", "some TV dialogue", "goodbye entity"]),
                         FakeBrain(), FakeTTS(), console=console)

    convo.turn()  # "stop listening" - now asleep
    lines.clear()
    convo.turn()

    assert not any("(listening" in line for line in lines)


def test_what_he_hears_is_in_the_record_even_when_the_terminal_does_not_show_it():
    # Reading a session back and seeing no check-ins made it look like none fired, when in fact they
    # had heard them - the record has to hold everything they heard, printed or not.
    recorded = []
    console = Console(echo=lambda _: None, overwrite=lambda _: None, record=recorded.append)
    convo = Conversation(FakeSTT(["hi"]), FakeBrain(), FakeTTS(), acknowledgement="ACK", console=console)

    convo.turn()

    assert "ACK" in recorded  # spoken, deliberately not printed, still recorded
    assert recorded.count("entity> reply to hi\n") == 1  # and a printed reply isn't recorded twice


def test_a_reply_cut_off_mid_utterance_is_noted_in_the_record():
    # "You didn't say that aloud. You only wrote it on the screen." - the record showed the line as
    # delivered, with nothing to say the voice was killed partway. Now the cut is on the record.
    recorded = []
    console = Console(echo=lambda _: None, overwrite=lambda _: None, record=recorded.append)
    interrupt = threading.Event()

    class CutOffTTS:
        def speak(self, text, *, interrupt=None):
            interrupt.set()  # the watcher (or Enter) kills the utterance partway through

    convo = Conversation(FakeSTT(["hi"]), FakeBrain(), CutOffTTS(),
                         acknowledgement="ACK", interrupt=interrupt, console=console)

    convo.turn()

    assert any("cut off" in line for line in recorded)


def test_a_voice_failure_is_noted_in_the_record_not_lost_to_stderr():
    recorded = []
    console = Console(echo=lambda _: None, overwrite=lambda _: None, record=recorded.append)

    class BrokenTTS:
        def speak(self, text, *, interrupt=None):
            raise RuntimeError("powershell exploded")

    convo = Conversation(FakeSTT(["hi"]), FakeBrain(), BrokenTTS(), acknowledgement="ACK", console=console)

    convo.turn()  # must not crash the loop

    assert any("voice failed" in line and "powershell exploded" in line for line in recorded)


def test_a_long_heads_up_is_offered_rather_than_read_out_in_full():
    # An agent's thirty-line report was read at them line after line, verbatim, with no warning -
    # the exact opposite of being insulated from the agent's internals.
    outbox = Outbox()
    outbox.push("WALL. " * 200)
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["goodbye entity"]), FakeBrain(), tts,
                         outbox=outbox, long_answer_chars=100)

    convo.turn()

    assert convo.ready_question in tts.spoken  # asked first
    assert not any(line.startswith("WALL.") for line in tts.spoken)  # nothing dumped


def test_several_queued_messages_are_delivered_as_one_thing_to_stop():
    outbox = Outbox()
    outbox.push("first agent finished")
    outbox.push("second agent finished")
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["goodbye entity"]), FakeBrain(), tts,
                         outbox=outbox, long_answer_chars=None)

    convo.turn()

    spoken = [line for line in tts.spoken if "agent finished" in line]
    assert len(spoken) == 1  # one utterance, so one STOP silences all of it
    assert "first agent finished" in spoken[0] and "second agent finished" in spoken[0]


def test_news_that_cannot_be_delivered_yet_does_not_wedge_the_loop():
    # THE FREEZE: an undeliverable message left outbox.arrived latched, and the window's mic yields
    # an empty turn whenever that flag is set - so the loop spun, their submissions were never read,
    # and only a restart got them out. Declining to deliver must never leave the flag standing.
    outbox = Outbox()
    convo = Conversation(FakeSTT(["tell me everything"]), VariableBrain(), FakeTTS(),
                         outbox=outbox, long_answer_chars=100)

    convo.turn()  # a long answer is offered, so an offer is now pending
    outbox.push("the fixer agent has news")
    convo._deliver_outbox()  # can't speak over a pending offer

    assert not outbox.arrived.is_set()  # nothing latched, so listening works normally


def test_news_held_back_is_delivered_once_it_can_be():
    outbox = Outbox()
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["tell me everything", "no thanks", "goodbye entity"]),
                         VariableBrain(), tts, outbox=outbox, long_answer_chars=100)

    convo.turn()  # offers
    outbox.push("the fixer agent has news")
    convo._deliver_outbox()  # held: an offer is pending
    convo.turn()  # "no thanks" clears the offer
    convo.turn()  # now it can be said

    assert any("fixer" in line for line in tts.spoken)  # held, not lost


def test_it_stays_quiet_while_the_mic_is_on():
    outbox = Outbox()
    outbox.push("the fixer agent has news")
    tts = FakeTTS()

    class MicSTT(FakeSTT):
        on = True

        def is_recording(self):
            return self.on

    stt = MicSTT(["", "goodbye entity"])
    convo = Conversation(stt, FakeBrain(), tts, outbox=outbox, long_answer_chars=None)

    convo.turn()
    assert not any("fixer" in line for line in tts.spoken)  # their mic is on; it does not speak

    stt.on = False
    convo.turn()
    assert any("fixer" in line for line in tts.spoken)


def test_a_long_reply_is_cut_short_rather_than_half_read():
    # They are never asked "ready for it?" any more, and they disliked hearing only part of what was
    # written: a reply is CUT at a sentence, so what they read is exactly what they hear.
    lines = []
    tts = FakeTTS()

    class WordyBrain:
        def respond(self, utterance):
            return ("First, the short answer. Second, some detail they can read. " + "Padding. " * 60)

    convo = Conversation(FakeSTT(["hi"]), WordyBrain(), tts, long_answer_chars=None,
                         spoken_chars=80, console=Console(echo=lines.append))

    convo.turn()

    shown = chr(10).join(lines)
    spoken = [line for line in tts.spoken if "First," in line][0]
    assert len(spoken) <= 100 and spoken.startswith("First, the short answer.")
    assert spoken in shown  # the same words on screen as in their ear
    assert "Padding. Padding. Padding." not in shown  # the wall never reaches them at all
    assert convo.ready_question not in tts.spoken  # and they were never asked


def test_a_reply_that_was_cut_is_reported_back_on_the_next_turn():
    # Truncating silently taught it nothing: it never saw the cut, so the next turn began with no
    # evidence any of it happened. Now the cut is part of what it hears next.
    heard = []

    class WordyBrain:
        def respond(self, utterance):
            heard.append(utterance)
            return "First. Second. " + "Padding. " * 60

    convo = Conversation(FakeSTT(["hi", "and again"]), WordyBrain(), FakeTTS(),
                         long_answer_chars=None, spoken_chars=60)

    convo.turn()
    convo.turn()

    assert heard[0] == "hi"  # the first turn is untouched
    assert "CUT OFF" in heard[1] and "and again" in heard[1]  # the second carries the consequence


def test_a_reply_that_fitted_is_not_complained_about():
    heard = []

    class BriefBrain:
        def respond(self, utterance):
            heard.append(utterance)
            return "Short enough."

    convo = Conversation(FakeSTT(["hi", "again"]), BriefBrain(), FakeTTS(),
                         long_answer_chars=None, spoken_chars=60)

    convo.turn()
    convo.turn()

    assert heard == ["hi", "again"]  # nothing added when nothing was lost
