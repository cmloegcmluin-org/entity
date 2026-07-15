import re
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
    ):
        self._stt = stt
        self._brain = brain
        self._tts = tts
        self._farewells = frozenset(_canonical(f) for f in farewells)
        self.farewell_reply = farewell_reply
        self.error_reply = error_reply

    def _is_farewell(self, heard):
        return _canonical(heard) in self._farewells

    def turn(self):
        heard = self._stt.listen()
        if not heard.strip():
            return None
        if self._is_farewell(heard):
            self._tts.speak(self.farewell_reply)
            return Turn(heard=heard, said=self.farewell_reply, farewell=True)
        try:
            said = self._brain.respond(heard)
        except Exception:
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
