import re
import sys
import threading
import time
from dataclasses import dataclass

DEFAULT_FAREWELLS = (
    "goodbye entity",
    "goodnight entity",
    "stop listening",
    "that's all for now",
    "quit",
    "exit",
)
DEFAULT_FAREWELL_REPLY = "Talk soon."
DEFAULT_ERROR_REPLY = "Sorry, my mind glitched for a second - say that again?"
DEFAULT_SUSPEND_REPLY = "Paused. Say resume when you're back."
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
    return f"Still working on it - {_humanize_elapsed(seconds)} so far."


def _canonical(text):
    """Lowercase, strip punctuation, collapse whitespace — so 'Goodbye, Entity.' matches 'goodbye entity'."""
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


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
        farewell_reply=DEFAULT_FAREWELL_REPLY,
        error_reply=DEFAULT_ERROR_REPLY,
        suspend_reply=DEFAULT_SUSPEND_REPLY,
        resume_reply=DEFAULT_RESUME_REPLY,
        acknowledgement=DEFAULT_ACK,
        reassurer=None,
        patience=DEFAULT_PATIENCE,
        check_in=DEFAULT_CHECK_IN,
        outbox=None,
    ):
        self._stt = stt
        self._brain = brain
        self._tts = tts
        self._farewells = frozenset(_canonical(f) for f in farewells)
        self.farewell_reply = farewell_reply
        self.error_reply = error_reply
        self.suspend_reply = suspend_reply
        self.resume_reply = resume_reply
        self._acknowledgement = acknowledgement
        self._reassure = reassurer or _default_reassurance
        self._patience = patience
        self._check_in = check_in
        self._outbox = outbox
        self._paused = False

    def _is_farewell(self, heard):
        return _canonical(heard) in self._farewells

    def _deliver_outbox(self):
        """Speak anything the Entity has queued to say on its own (word from an agent). Called when
        it's the Entity's turn to talk - at a lull, or right after he finishes - never mid-sentence,
        because a `listen()` in progress only breaks off for this once he's paused, not while he's
        speaking."""
        if self._outbox is None:
            return
        for message in self._outbox.drain():
            self._tts.speak(message)

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
            self._tts.speak(self._reassure(time.monotonic() - start))
            wait_for = self._check_in
        if "error" in outcome:
            raise outcome["error"]
        return outcome["reply"]

    def turn(self):
        self._deliver_outbox()  # say any queued agent news before we start listening again
        heard = self._stt.listen()
        if not heard.strip():
            return None
        if self._is_farewell(heard):
            self._tts.speak(self.farewell_reply)
            return Turn(heard=heard, said=self.farewell_reply, farewell=True)
        canonical = _canonical(heard)
        if self._paused:
            if canonical == "resume":
                self._paused = False
                self._tts.speak(self.resume_reply)
                return Turn(heard=heard, said=self.resume_reply)
            return None  # while paused, ignore everything except "resume" (and farewell above)
        if canonical == "suspend":
            self._paused = True
            self._tts.speak(self.suspend_reply)
            return Turn(heard=heard, said=self.suspend_reply)
        self._tts.speak(self._acknowledgement)  # let him know he was heard before the thinking pause
        try:
            said = self._think(heard)
        except Exception as exc:  # surface the real cause instead of a silent "glitch"
            print(f"[brain error] {exc!r}", file=sys.stderr)
            self._tts.speak(self.error_reply)
            return Turn(heard=heard, said=self.error_reply, error=True)
        self._tts.speak(said)
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
