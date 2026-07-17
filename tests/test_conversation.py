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

    class InterruptingBrain:  # he hits Enter while it's thinking
        def respond(self, utterance):
            interrupt.set()
            return "a fifteen-minute novella he never wanted"

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
    assert events.index(printed) < events.index("say:reply to hi")  # he can read it before/while it speaks


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


def test_it_pauses_after_a_reply_to_give_him_a_beat_to_read():
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
            interrupt.set()  # he cuts in as the reply lands
            return "reply"

    convo = Conversation(
        FakeSTT(["hi"]), InterruptingBrain(), FakeTTS(),
        acknowledgement="ACK", read_pause=1.5, sleep=slept.append, interrupt=interrupt,
    )

    convo.turn()

    assert slept == []  # he's cutting in - don't make him wait out a read pause


def test_is_affirmative_reads_a_yes_but_not_a_no():
    assert _is_affirmative("yes") and _is_affirmative("yeah go ahead") and _is_affirmative("sure, hit me")
    assert _is_affirmative("okay") and _is_affirmative("let's hear it")
    assert not _is_affirmative("no thanks") and not _is_affirmative("not now") and not _is_affirmative("nope")
    assert not _is_affirmative("maybe later")  # "later" is a decline, not a yes


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
    assert VariableBrain.WALL not in tts.spoken  # nothing dumped yet

    convo.turn()  # he says "yes"

    assert VariableBrain.WALL in tts.spoken  # the yes released the full answer


def test_a_declined_long_answer_is_dropped_and_the_new_utterance_is_handled():
    brain = VariableBrain()
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["tell me everything", "no thanks"]), brain, tts,
                         acknowledgement="ACK", long_answer_chars=100)

    convo.turn()  # offers "ready?"
    convo.turn()  # "no thanks"

    assert VariableBrain.WALL not in tts.spoken  # he declined, so it was never delivered
    assert "a short reply" in tts.spoken  # and "no thanks" was handled as an ordinary turn
    assert brain.heard == ["tell me everything", "no thanks"]


def test_gating_off_speaks_even_a_long_answer_straight_away():
    brain = VariableBrain()
    tts = FakeTTS()
    convo = Conversation(FakeSTT(["tell me everything"]), brain, tts,
                         acknowledgement="ACK", long_answer_chars=None)

    turn = convo.turn()

    assert turn.said == VariableBrain.WALL  # gate disabled -> spoken as before
    assert VariableBrain.WALL in tts.spoken


def test_a_slow_think_detaches_to_the_background_and_frees_the_loop():
    release = threading.Event()

    class SlowBrain:
        def respond(self, utterance):
            release.wait(2.0)  # never lands before the detach window
            return "the finished long-running answer"

    tts = FakeTTS()
    convo = Conversation(
        FakeSTT(["do the slow thing", "yes"]), SlowBrain(), tts,
        acknowledgement="ACK", detach_after=0.05, patience=30,
    )

    first = convo.turn()

    assert first is None  # it didn't block - the loop is free to listen again
    assert convo.detach_reply in tts.spoken  # he was told it's running in the background

    bg_done = convo._background["done"]
    release.set()
    assert bg_done.wait(2.0)  # the background call finishes on its own thread

    convo.turn()  # he comes back; the answer is offered, and his "yes" releases it

    assert convo.ready_question in tts.spoken
    assert "the finished long-running answer" in tts.spoken


def test_a_new_request_while_a_think_is_detached_is_deflected_not_run_concurrently():
    # One brain, one session: a second brain call must NOT overlap the detached one.
    release = threading.Event()
    calls = []

    class SlowBrain:
        def respond(self, utterance):
            calls.append(utterance)
            release.wait(2.0)
            return "done"

    tts = FakeTTS()
    convo = Conversation(
        FakeSTT(["slow one", "what about this"]), SlowBrain(), tts,
        acknowledgement="ACK", detach_after=0.05, patience=30, busy_reply="BUSY",
    )

    convo.turn()  # detaches
    second = convo.turn()  # a new ask while the first is still running

    assert second.said == "BUSY"  # deflected
    assert calls == ["slow one"]  # the second request did not start a concurrent brain call
    release.set()


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
            return "an essay he never wanted to sit through"

        def interrupt(self):
            self.interrupted = True
            release.set()  # cancelling unblocks the call, as the real SDK interrupt does

    brain = SlowInterruptibleBrain()
    tts = FakeTTS()

    def barge():
        thinking.wait(2.0)
        interrupt.set()  # he hits Enter / says "stop" while it's still thinking

    threading.Thread(target=barge, daemon=True).start()
    convo = Conversation(
        FakeSTT(["do the big thing"]), brain, tts,
        acknowledgement="ACK", interrupt=interrupt, patience=30,
    )
    turn = convo.turn()

    assert turn is None  # the turn was abandoned - the loop is free to listen again
    assert brain.interrupted is True  # the brain was told to drop the in-flight call
    assert "an essay he never wanted to sit through" not in tts.spoken  # cancelled reply stayed unsaid
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
            return "a monologue he tried to stop"

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
    assert "a monologue he tried to stop" not in tts.spoken


def test_check_ins_still_fire_while_a_stop_watcher_holds_the_mic():
    # A slow think runs a stop-watcher on the mic; a spoken check-in must not need its own second
    # watcher (two readers corrupt one mic) yet must still be spoken.
    class QuietMicSTT:
        def listen(self):
            return "go"

        def catch_stop(self, active):
            while active():
                time.sleep(0.005)
            return False  # he never says stop

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

    class StopHearingSTT:  # he says "stop" the instant it starts talking
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

    assert tts.spoken[:2] == ["first", "second"]


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
    # he said only "over"; the turn is empty but the terminator registered, so acknowledge it out
    # loud instead of ignoring him - otherwise he just repeats "over" wondering if he was heard.
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
