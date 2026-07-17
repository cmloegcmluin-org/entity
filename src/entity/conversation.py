import random
import re
import sys
import threading
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
# dead air waiting to find out he was heard. Kept short and varied; a single canned line said every
# turn is exactly the "pre-packaged" tic he called out, so the default picker never repeats itself
# back to back.
DEFAULT_ACKS = (
    "Got it.",
    "Mm-hm.",
    "Okay.",
    "Right.",
    "Gotcha.",
    "Sure thing.",
    "Let me think.",
    "One sec.",
    "On it.",
    "Hmm, okay.",
)

# When a reply is taking a while, say something so a long think doesn't read as a crash - the exact
# fear he's had ("feels crashed then finally responds"). Only fires past DEFAULT_PATIENCE seconds,
# so a normal quick turn never hears it; varied for the same reason the acks are.
DEFAULT_PATIENCE = 6.0
DEFAULT_REASSURANCES = (
    "Still with you.",
    "Almost there.",
    "One more moment.",
    "Still on it.",
    "Bear with me.",
)


def _make_picker(pool, rng=None):
    """A zero-arg picker that returns a line from the pool, never the same one twice running."""
    rng = rng or random.Random()
    last = None

    def pick():
        nonlocal last
        choices = [line for line in pool if line != last] or list(pool)
        last = rng.choice(choices)
        return last

    return pick


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
        acknowledger=None,
        reassurer=None,
        patience=DEFAULT_PATIENCE,
    ):
        self._stt = stt
        self._brain = brain
        self._tts = tts
        self._farewells = frozenset(_canonical(f) for f in farewells)
        self.farewell_reply = farewell_reply
        self.error_reply = error_reply
        self.suspend_reply = suspend_reply
        self.resume_reply = resume_reply
        self._acknowledge = acknowledger or _make_picker(DEFAULT_ACKS)
        self._reassure = reassurer or _make_picker(DEFAULT_REASSURANCES)
        self._patience = patience
        self._paused = False

    def _is_farewell(self, heard):
        return _canonical(heard) in self._farewells

    def _think(self, heard):
        """Ask the brain off the main thread so, if the answer is slow to come, we can speak a
        reassurance instead of leaving him in silence wondering if it crashed. Re-raises whatever
        the brain raised, so the caller's error handling is unchanged."""
        outcome = {}
        done = threading.Event()

        def work():
            try:
                outcome["reply"] = self._brain.respond(heard)
            except BaseException as exc:  # carry it back to the main thread to re-raise in context
                outcome["error"] = exc
            finally:
                done.set()

        threading.Thread(target=work, daemon=True).start()
        if not done.wait(self._patience):  # still thinking after a while - say so, then keep waiting
            self._tts.speak(self._reassure())
            done.wait()
        if "error" in outcome:
            raise outcome["error"]
        return outcome["reply"]

    def turn(self):
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
        self._tts.speak(self._acknowledge())  # let him know he was heard before the thinking pause
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
