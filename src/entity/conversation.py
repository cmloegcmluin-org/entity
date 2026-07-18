import re
import sys
import threading
import time
from dataclasses import dataclass

from entity.console import Console

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
DEFAULT_FAREWELL_REPLY = "Be seeing you."
DEFAULT_ERROR_REPLY = "Sorry, my mind glitched for a second - say that again?"
# He ended a turn ("over") but said nothing in it. Rather than ignore him - which just makes him
# repeat "over" wondering if he was heard - acknowledge that the turn registered and invite him on.
DEFAULT_EMPTY_TURN_REPLY = "Go ahead."
DEFAULT_SUSPEND_REPLY = "Resting. Say 'hey Entity' when you want me back."
DEFAULT_RESUME_REPLY = "Back with you."

# A long or slow answer isn't dumped on him - it's offered first, and only spoken once he says yes,
# so a wall of text (or a reply he's stopped caring about) never just barges out of the speaker.
DEFAULT_READY_QUESTION = "I've got a longer answer for you - ready for it?"
# Replies over this many characters are gated behind DEFAULT_READY_QUESTION. Set to None to never
# gate. Kept a few sentences long, since the persona already pushes hard for brevity - only a
# genuinely big reply should have to wait for a yes.
DEFAULT_LONG_ANSWER_CHARS = 320

# Whether he said yes to "ready for it?". A negative word anywhere vetoes it (so "okay, no" is a no);
# otherwise any of the yes words counts. Deliberately generous on yes and strict on no - a false yes
# just speaks something he half-wanted, a false no makes him repeat himself.
_AFFIRMATIVES = (
    "yes", "yeah", "yep", "yup", "sure", "okay", "ok", "ready", "please", "now",
    "go ahead", "go for it", "do it", "hit me", "hear it", "let's hear", "sounds good", "please do",
)
_NEGATIVES = ("no", "nope", "nah", "not", "dont", "later", "wait", "hold", "stop", "skip")

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

# Once a think has run this long it stops blocking the conversation: the Entity says it'll keep at
# it, the call runs on in the background, and the finished answer is offered later. None disables
# detaching (a slow think just blocks with check-ins, as before). Well past the check-in cadence so
# only a genuinely long call detaches.
DEFAULT_DETACH_AFTER = 45.0
DEFAULT_DETACH_REPLY = "This one'll take me a while - I'll keep at it and let you know when it's ready."
# One brain, one session: while a detached call is still running, a new request can't start a second
# one, so it's deflected with this until the first lands.
DEFAULT_BUSY_REPLY = "Still finishing your last one - give me a moment."

# After a reply, wait this long before listening again, so he gets a beat to read it rather than the
# mic reopening the instant the voice stops. 0 disables (default; the app turns it on for voice runs).
DEFAULT_READ_PAUSE = 0.0


class _ThinkInterrupted(Exception):
    """Internal signal that a barge-in cancelled the brain call - the turn is abandoned and the
    loop goes straight back to listening, with no reply and no error spoken."""


class _ThinkDetached(Exception):
    """Internal signal that a slow think was handed to the background - the turn ends and the loop
    listens again; the finished answer is offered on a later turn."""


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


def _is_affirmative(heard):
    """Did he say yes to an offer? Any negative word veto-es it; otherwise any yes word counts."""
    canonical = _canonical(heard)
    words = canonical.split()
    if any(neg in words for neg in _NEGATIVES):
        return False
    return any(yes in words if " " not in yes else yes in canonical for yes in _AFFIRMATIVES)


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
        ready_question=DEFAULT_READY_QUESTION,
        detach_reply=DEFAULT_DETACH_REPLY,
        busy_reply=DEFAULT_BUSY_REPLY,
        acknowledgement=DEFAULT_ACK,
        reassurer=None,
        patience=DEFAULT_PATIENCE,
        check_in=DEFAULT_CHECK_IN,
        interrupt_poll=DEFAULT_INTERRUPT_POLL,
        cancel_wait=DEFAULT_CANCEL_WAIT,
        detach_after=DEFAULT_DETACH_AFTER,
        long_answer_chars=DEFAULT_LONG_ANSWER_CHARS,
        read_pause=DEFAULT_READ_PAUSE,
        console=None,
        sleep=time.sleep,
        timings=False,
        outbox=None,
        interrupt=None,
        wake=None,
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
        self.ready_question = ready_question
        self.detach_reply = detach_reply
        self.busy_reply = busy_reply
        self._long_answer_chars = long_answer_chars
        self._detach_after = detach_after
        self._offered = None  # a long/slow answer spoken only once he says yes to "ready for it?"
        self._background = None  # a slow think handed off, still running: {"done", "outcome"}
        self._wake = wake  # event the mic waits on; set to break a lull when news is ready to speak
        self._acknowledgement = acknowledgement
        self._reassure = reassurer or _default_reassurance
        self._patience = patience
        self._check_in = check_in
        self._interrupt_poll = interrupt_poll
        self._cancel_wait = cancel_wait
        self._read_pause = read_pause  # a beat after a reply so he can read it before listening resumes
        self._console = console or Console()
        self._sleep = sleep
        self._timings = timings  # --timings: show how long each turn spent thinking vs. speaking
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

    def _speak_reply(self, text):
        """Print the line to the terminal, then speak it - so he can read the reply as it's said,
        not only hear it go by. Used for whatever a turn returns as its `said`; the ack and check-ins
        stay terminal-silent."""
        self._console.reply(text)
        self._say(text)

    def _pause_to_read(self):
        """A short beat after a reply before the mic reopens, so he isn't rushed off it - skipped if
        he's barged in (he's cutting in, not reading)."""
        if self._read_pause > 0 and not self._interrupted():
            self._sleep(self._read_pause)

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
            self._console.heads_up(message)  # to the terminal too, not only spoken
            self._say(message)

    def _think(self, heard):
        """Ask the brain off the main thread so a slow reply can't read as a crash. The first
        check-in comes after `patience`, then it keeps checking in every `check_in` seconds - each
        time saying how long it's been - until the reply lands. If he barges in while it's thinking,
        the call is cancelled and `_ThinkInterrupted` is raised so the loop drops the turn. If it
        runs past `detach_after`, it's handed to the background and `_ThinkDetached` is raised so the
        loop is freed and the answer is offered later. Re-raises whatever the brain raised, so the
        caller's error handling is unchanged."""
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
            detach_at = start + self._detach_after if self._detach_after is not None else None
            while not done.is_set():
                if self._interrupted():  # he cut in - cancel the call and abandon the turn
                    self._cancel_think(done)
                    raise _ThinkInterrupted
                if detach_at is not None and time.monotonic() >= detach_at:  # too slow - background it
                    self._speak_reply(self.detach_reply)
                    self._detach(done, outcome)
                    raise _ThinkDetached
                deadline = next_check_in if detach_at is None else min(next_check_in, detach_at)
                timeout = min(self._interrupt_poll, max(0.0, deadline - time.monotonic()))
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

    def _detach(self, done, outcome):
        """Leave the slow call running on its worker and remember it; a reaper breaks the next lull
        the moment it lands, so the finished answer gets offered promptly rather than waiting for him
        to speak first."""
        self._background = {"done": done, "outcome": outcome}
        if self._wake is not None:
            threading.Thread(target=self._reap, args=(done,), daemon=True).start()

    def _reap(self, done):
        done.wait()
        self._wake.set()  # break the mic's lull so the loop cycles round and offers the answer

    def _collect_background(self):
        """If a detached call has finished, take its answer and offer it (a failure is dropped - a
        background best-effort, not worth surfacing as a glitch). Runs at the top of a turn."""
        background = self._background
        if background is None or not background["done"].is_set():
            return
        self._background = None
        reply = background["outcome"].get("reply")
        if reply is not None and self._offered is None:
            self._offered = reply
            self._speak_reply(self.ready_question)

    def turn(self):
        if self._interrupt is not None:
            self._interrupt.clear()  # a fresh turn; forget any leftover "stop" from the last one
        self._deliver_outbox()  # say any queued agent news before we start listening again
        self._collect_background()  # a slow answer that has since landed is offered here
        heard = self._stt.listen()
        if not heard.strip():
            # An empty turn that still ended on "over" means he said only the terminator - let him
            # know it registered (the "✓ got it" cue already printed) instead of leaving dead air.
            if getattr(self._stt, "caught_terminator", False):
                self._say(self.empty_turn_reply)
            return None
        self._console.heard(heard)  # show what was transcribed before we act on it
        if self._is_farewell(heard):
            self._speak_reply(self.farewell_reply)
            return Turn(heard=heard, said=self.farewell_reply, farewell=True)
        canonical = _canonical(heard)
        if self._paused:
            if _ends_with_command(canonical, self._resumes):  # "hey entity" wakes it back up
                self._paused = False
                self._speak_reply(self.resume_reply)
                return Turn(heard=heard, said=self.resume_reply)
            return None  # while asleep, ignore everything except a wake word (and farewell above)
        if _ends_with_command(canonical, self._suspends):  # "stop listening" puts it to sleep, doesn't quit
            self._paused = True
            self._speak_reply(self.suspend_reply)
            return Turn(heard=heard, said=self.suspend_reply)
        if self._offered is not None:  # he's answering "ready for it?" from a held long/slow reply
            return self._resolve_offer(heard)
        if self._background is not None:  # a detached call is still running - one session, so wait it out
            self._speak_reply(self.busy_reply)
            return Turn(heard=heard, said=self.busy_reply)
        return self._answer(heard)

    def _answer(self, heard):
        """Acknowledge, think, and speak the reply - unless it's long enough to gate, in which case
        it's held and offered first (see _offer)."""
        self._say(self._acknowledgement)  # let him know he was heard before the thinking pause
        self._console.thinking()  # a "(thinking…)" indicator so a pause doesn't read as a hang
        think_start = time.monotonic()
        try:
            said = self._think(heard)
        except _ThinkInterrupted:  # he cut the thinking off - no reply, straight back to listening
            return None
        except _ThinkDetached:  # too slow - it's running in the background; offered when it lands
            return None
        except Exception as exc:  # surface the real cause instead of a silent "glitch"
            print(f"[brain error] {exc!r}", file=sys.stderr)
            self._speak_reply(self.error_reply)
            return Turn(heard=heard, said=self.error_reply, error=True)
        think_time = time.monotonic() - think_start
        if self._should_gate(said):
            return self._offer(heard, said)
        speak_start = time.monotonic()
        self._speak_reply(said)  # if he hit Enter while it was talking, this is cut off
        if self._timings:
            self._console.timing(think=think_time, speak=time.monotonic() - speak_start)
        self._pause_to_read()
        return Turn(heard=heard, said=said)

    def _should_gate(self, reply):
        return self._long_answer_chars is not None and len(reply) > self._long_answer_chars

    def _offer(self, heard, answer):
        """Hold a long answer and ask if he wants it, rather than dumping it. His next turn's yes
        releases it (see _resolve_offer)."""
        self._offered = answer
        self._speak_reply(self.ready_question)
        return Turn(heard=heard, said=self.ready_question)

    def _resolve_offer(self, heard):
        """His reply to "ready for it?": a yes speaks the held answer; anything else drops it and the
        utterance is handled as an ordinary new turn, so he's never stuck on the offer."""
        answer, self._offered = self._offered, None
        if _is_affirmative(heard):
            self._speak_reply(answer)
            self._pause_to_read()
            return Turn(heard=heard, said=answer)
        return self._answer(heard)

    def run(self, should_continue=lambda: True, on_turn=None):
        while should_continue():
            result = self.turn()
            if result is None:
                continue
            if on_turn is not None:
                on_turn(result)
            if result.farewell:
                break
