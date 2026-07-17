import re
import sys
import threading
import time
from dataclasses import dataclass

DEFAULT_FAREWELLS = (
    "goodbye entity",
    "goodnight entity",
    "that's all for now",
    "quit",
    "exit",
)
# "Stop listening" doesn't quit - it puts the Entity to sleep so it stops responding; "hey entity"
# wakes it. (While asleep it still transcribes, only to catch the wake word - nothing reaches the brain.)
DEFAULT_SUSPENDS = ("suspend", "stop listening")
DEFAULT_RESUMES = ("resume", "hey entity")
DEFAULT_FAREWELL_REPLY = "Talk soon."
DEFAULT_ERROR_REPLY = "Sorry, my mind glitched for a second - say that again?"
# He ended a turn ("over") but said nothing in it. Rather than ignore him - which just makes him
# repeat "over" wondering if he was heard - acknowledge that the turn registered and invite him on.
DEFAULT_EMPTY_TURN_REPLY = "Go ahead."
DEFAULT_SUSPEND_REPLY = "Resting. Say 'hey Entity' when you want me back."
DEFAULT_RESUME_REPLY = "Back with you."

# Spoken the instant a turn is heard, before the brain even starts - so the user never talks into
# dead air waiting to find out he was heard. One plain line he asked for by name (the varied ones
# came out as awkward TTS - "Mm-hm." read aloud as "m m").
DEFAULT_ACK = "Message received."

# A long reply must never feel like a crash. The first "still working" comes after DEFAULT_PATIENCE;
# after that it repeats at least every DEFAULT_CHECK_IN, each time saying how long it's been (the
# model gives no real progress percentage to report, so elapsed time is the honest stand-in).
DEFAULT_PATIENCE = 6.0
DEFAULT_CHECK_IN = 30.0

# While the brain thinks, re-check this often for a barge-in, so cutting a slow think off feels
# instant rather than waiting out the next check-in.
DEFAULT_INTERRUPT_POLL = 0.05
# After telling the brain to cancel, wait up to this long for the call to actually unwind before
# moving on - so the loop never starts a second brain call overlapping a half-cancelled one.
DEFAULT_CANCEL_WAIT = 10.0


class _ThinkInterrupted(Exception):
    """Internal signal that a barge-in cancelled the brain call - the turn is abandoned and the
    loop goes straight back to listening, with no reply and no error spoken."""


def _humanize_elapsed(seconds):
    seconds = max(5, int(round(seconds / 5.0)) * 5)  # nearest 5s, never below 5
    if seconds < 60:
        return f"about {seconds} seconds"
    minutes, rest = divmod(seconds, 60)
    unit = "minute" if minutes == 1 else "minutes"
    if rest == 0:
        return f"about {minutes} {unit}"
    return f"about {minutes} {unit} and {rest} seconds"


def _default_reassurance(seconds):
    # "processing your request", not "working on it" - the Entity triages and relays, it isn't doing
    # the agent's actual work, and he found "working on it" misleading.
    return f"Still processing your request - {_humanize_elapsed(seconds)} so far."


def _canonical(text):
    """Lowercase, strip punctuation, collapse whitespace — so 'Goodbye, Entity.' matches 'goodbye entity'."""
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _ends_with_command(canonical, commands):
    """A command counts if the utterance IS it or ENDS with it - so "okay, stop listening" trips
    "stop listening", not just the bare phrase. (He rarely says these distinctive phrases by
    accident, and transcription usually tacks a stray word on, which exact-match then missed.)"""
    return any(canonical == cmd or canonical.endswith(" " + cmd) for cmd in commands)


@dataclass(frozen=True)
class Turn:
    heard: str
    said: str
    farewell: bool = False
    error: bool = False


class Conversation:
    """Ties speech-to-text, a brain, and text-to-speech into a listen -> think -> speak loop."""

    def __init__(
        self,
        stt,
        brain,
        tts,
        *,
        farewells=DEFAULT_FAREWELLS,
        suspends=DEFAULT_SUSPENDS,
        resumes=DEFAULT_RESUMES,
        farewell_reply=DEFAULT_FAREWELL_REPLY,
        error_reply=DEFAULT_ERROR_REPLY,
        suspend_reply=DEFAULT_SUSPEND_REPLY,
        resume_reply=DEFAULT_RESUME_REPLY,
        empty_turn_reply=DEFAULT_EMPTY_TURN_REPLY,
        acknowledgement=DEFAULT_ACK,
        reassurer=None,
        patience=DEFAULT_PATIENCE,
        check_in=DEFAULT_CHECK_IN,
        interrupt_poll=DEFAULT_INTERRUPT_POLL,
        cancel_wait=DEFAULT_CANCEL_WAIT,
        outbox=None,
        interrupt=None,
    ):
        self._stt = stt
        self._brain = brain
        self._tts = tts
        self._farewells = frozenset(_canonical(f) for f in farewells)
        self._suspends = frozenset(_canonical(s) for s in suspends)
        self._resumes = frozenset(_canonical(r) for r in resumes)
        self.farewell_reply = farewell_reply
        self.error_reply = error_reply
        self.suspend_reply = suspend_reply
        self.resume_reply = resume_reply
        self.empty_turn_reply = empty_turn_reply
        self._acknowledgement = acknowledgement
        self._reassure = reassurer or _default_reassurance
        self._patience = patience
        self._check_in = check_in
        self._interrupt_poll = interrupt_poll
        self._cancel_wait = cancel_wait
        self._outbox = outbox
        self._interrupt = interrupt  # set (e.g. by a keypress) to cut off whatever it's saying
        self._paused = False
        self._floor_watched = False  # true while a stop-watcher already holds the mic (see _say)

    def _is_farewell(self, heard):
        return _ends_with_command(_canonical(heard), self._farewells)

    def _interrupted(self):
        return self._interrupt is not None and self._interrupt.is_set()

    def _say(self, text):
        """Speak, unless he's cut in. Once the interrupt is set, every later line this turn stays
        unsaid, and a line already in progress is killed by the TTS. While it speaks, a background
        watcher listens for him saying "stop", which trips the same interrupt - so he can cut it off
        by voice, not just the Enter key. When a watcher already holds the mic (a check-in spoken
        mid-think), we don't open a second one - two readers on one mic corrupt each other. A voice
        hiccup is logged, not fatal - a failed utterance must never crash the loop (it did, and he
        lost the whole run)."""
        if self._interrupted():
            return
        stop_watching = None if self._floor_watched else self._watch_for_spoken_stop()
        try:
            self._tts.speak(text, interrupt=self._interrupt)
        except Exception as exc:
            print(f"[tts error] {exc!r}", file=sys.stderr)
        finally:
            if stop_watching is not None:
                stop_watching()

    def _watch_for_spoken_stop(self):
        """If the mic can catch a spoken stop word, listen for one for as long as we're speaking and
        set the interrupt when it lands. Returns a callable that stops and joins the watcher (so the
        mic is free again before the next listen), or None when voice-stop isn't available."""
        catch_stop = getattr(self._stt, "catch_stop", None)
        if catch_stop is None or self._interrupt is None:
            return None
        speaking = threading.Event()
        speaking.set()

        def watch():
            try:
                if catch_stop(speaking.is_set):  # he said "stop" while it was talking
                    self._interrupt.set()
            except Exception as exc:
                print(f"[voice-stop error] {exc!r}", file=sys.stderr)

        thread = threading.Thread(target=watch, daemon=True)
        thread.start()

        def stop():
            speaking.clear()  # the reply's done (or was cut) - let the watcher release the mic
            thread.join(timeout=1.5)

        return stop

    def _deliver_outbox(self):
        """Speak anything the Entity has queued to say on its own (word from an agent). Called when
        it's the Entity's turn to talk - at a lull, or right after he finishes - never mid-sentence,
        because a `listen()` in progress only breaks off for this once he's paused, not while he's
        speaking."""
        if self._outbox is None:
            return
        for message in self._outbox.drain():
            print(f"entity (heads-up)> {message}\n", flush=True)  # to the terminal too, not only spoken
            self._say(message)

    def _think(self, heard):
        """Ask the brain off the main thread so a slow reply can't read as a crash. The first
        check-in comes after `patience`, then it keeps checking in every `check_in` seconds - each
        time saying how long it's been - until the reply lands. If he barges in while it's thinking,
        the call is cancelled and `_ThinkInterrupted` is raised so the loop drops the turn and goes
        back to listening. Re-raises whatever the brain raised, so the caller's error handling is
        unchanged."""
        outcome = {}
        done = threading.Event()

        def work():
            try:
                outcome["reply"] = self._brain.respond(heard)
            except BaseException as exc:  # carry it back to the main thread to re-raise in context
                outcome["error"] = exc
            finally:
                done.set()

        start = time.monotonic()
        threading.Thread(target=work, daemon=True).start()
        # Listen for a spoken "stop" for the whole think, not just during a check-in - so he can cut
        # off a slow brain call by voice even in its silent stretches, the same as pressing Enter.
        stop_watching = self._watch_for_spoken_stop()
        self._floor_watched = stop_watching is not None
        try:
            next_check_in = start + self._patience
            while not done.is_set():
                if self._interrupted():  # he cut in - cancel the call and abandon the turn
                    self._cancel_think(done)
                    raise _ThinkInterrupted
                timeout = min(self._interrupt_poll, max(0.0, next_check_in - time.monotonic()))
                if done.wait(timeout):
                    break
                now = time.monotonic()
                if now >= next_check_in:  # still thinking - tell him how long, then keep waiting
                    self._say(self._reassure(now - start))
                    next_check_in = now + self._check_in
        finally:
            self._floor_watched = False
            if stop_watching is not None:
                stop_watching()
        if "error" in outcome:
            raise outcome["error"]
        return outcome["reply"]

    def _cancel_think(self, done):
        """Tell the brain to drop the in-flight call, then wait for the worker to unwind before
        returning - so the next turn never starts a second brain call overlapping this one. A brain
        with no `interrupt` (e.g. a fake) can't be cancelled; we still wait out the bounded window."""
        interrupt = getattr(self._brain, "interrupt", None)
        if interrupt is not None:
            try:
                interrupt()
            except Exception as exc:
                print(f"[interrupt error] {exc!r}", file=sys.stderr)
        done.wait(self._cancel_wait)

    def turn(self):
        if self._interrupt is not None:
            self._interrupt.clear()  # a fresh turn; forget any leftover "stop" from the last one
        self._deliver_outbox()  # say any queued agent news before we start listening again
        heard = self._stt.listen()
        if not heard.strip():
            # An empty turn that still ended on "over" means he said only the terminator - let him
            # know it registered (the "✓ got it" cue already printed) instead of leaving dead air.
            if getattr(self._stt, "caught_terminator", False):
                self._say(self.empty_turn_reply)
            return None
        if self._is_farewell(heard):
            self._say(self.farewell_reply)
            return Turn(heard=heard, said=self.farewell_reply, farewell=True)
        canonical = _canonical(heard)
        if self._paused:
            if _ends_with_command(canonical, self._resumes):  # "hey entity" wakes it back up
                self._paused = False
                self._say(self.resume_reply)
                return Turn(heard=heard, said=self.resume_reply)
            return None  # while asleep, ignore everything except a wake word (and farewell above)
        if _ends_with_command(canonical, self._suspends):  # "stop listening" puts it to sleep, doesn't quit
            self._paused = True
            self._say(self.suspend_reply)
            return Turn(heard=heard, said=self.suspend_reply)
        self._say(self._acknowledgement)  # let him know he was heard before the thinking pause
        try:
            said = self._think(heard)
        except _ThinkInterrupted:  # he cut the thinking off - no reply, straight back to listening
            return None
        except Exception as exc:  # surface the real cause instead of a silent "glitch"
            print(f"[brain error] {exc!r}", file=sys.stderr)
            self._say(self.error_reply)
            return Turn(heard=heard, said=self.error_reply, error=True)
        self._say(said)  # if he hit Enter while it was talking, this is cut off
        return Turn(heard=heard, said=said)

    def run(self, should_continue=lambda: True, on_turn=None):
        while should_continue():
            result = self.turn()
            if result is None:
                continue
            if on_turn is not None:
                on_turn(result)
            if result.farewell:
                break
