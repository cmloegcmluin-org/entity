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
        acknowledgement=DEFAULT_ACK,
        reassurer=None,
        patience=DEFAULT_PATIENCE,
        check_in=DEFAULT_CHECK_IN,
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
        self._acknowledgement = acknowledgement
        self._reassure = reassurer or _default_reassurance
        self._patience = patience
        self._check_in = check_in
        self._outbox = outbox
        self._interrupt = interrupt  # set (e.g. by a keypress) to cut off whatever it's saying
        self._paused = False

    def _is_farewell(self, heard):
        return _ends_with_command(_canonical(heard), self._farewells)

    def _interrupted(self):
        return self._interrupt is not None and self._interrupt.is_set()

    def _say(self, text):
        """Speak, unless he's cut in. Once the interrupt is set, every later line this turn stays
        unsaid, and a line already in progress is killed by the TTS. While it speaks, a background
        watcher listens for him saying "stop", which trips the same interrupt - so he can cut it off
        by voice, not just the Enter key. A voice hiccup is logged, not fatal - a failed utterance
        must never crash the loop (it did, and he lost the whole run)."""
        if self._interrupted():
            return
        stop_watching = self._watch_for_spoken_stop()
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
            self._say(message)

    def _think(self, heard):
        """Ask the brain off the main thread so a slow reply can't read as a crash. The first
        check-in comes after `patience`, then it keeps checking in every `check_in` seconds -
        each time saying how long it's been - until the reply lands. Re-raises whatever the brain
        raised, so the caller's error handling is unchanged."""
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
        wait_for = self._patience
        while not done.wait(wait_for):  # still thinking - tell him how long, then keep waiting
            self._say(self._reassure(time.monotonic() - start))
            wait_for = self._check_in
        if "error" in outcome:
            raise outcome["error"]
        return outcome["reply"]

    def turn(self):
        if self._interrupt is not None:
            self._interrupt.clear()  # a fresh turn; forget any leftover "stop" from the last one
        self._deliver_outbox()  # say any queued agent news before we start listening again
        heard = self._stt.listen()
        if not heard.strip():
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
        except Exception as exc:  # surface the real cause instead of a silent "glitch"
            print(f"[brain error] {exc!r}", file=sys.stderr)
            self._say(self.error_reply)
            return Turn(heard=heard, said=self.error_reply, error=True)
        self._say(said)  # if he hit Enter while it was thinking or talking, this is cut off
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
