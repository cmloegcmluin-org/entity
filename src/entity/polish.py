"""The cleanup pass between his voice and the brain: pauses out, sentences back.

His natural pauses get transcribed as sentence breaks - "periods and capitalization on next word
even though they aren't natural points to end sentences" - so what reaches the brain reads like
chopped-up fragments and he has to massage the draft by hand. His call on where the fix runs:
"before submitting... hopefully only a second of wait time." So one small fast session, opened at
startup and kept warm, repairs ONLY the punctuation of the submitted draft, and the loop waits on
it briefly - never long, and never at the cost of his words:

- Bounded: past the deadline the raw text goes through as typed. A slow model may not stall a turn.
- Word-safe by CODE, not by trust: if the repaired text's letters and digits are not exactly the
  raw text's letters and digits, the repair is thrown away. The model is ASKED to change only
  punctuation; this is what makes it unable to eat a word no matter what it answers.
"""

import re
import threading

from claude_agent_sdk import ClaudeAgentOptions

from entity.models import FAMILIES
from entity.sdk_session import SdkSession

POLISH_MODEL = FAMILIES["haiku"]  # a punctuation pass: the smallest, fastest tier there is

# How long the submit will wait before letting the raw text through. His acceptance was "hopefully
# only a second"; the deadline is above that so a normal repair fits, and a hung one never holds
# a turn hostage.
POLISH_DEADLINE = 3.0

PROMPT = (
    "Dictated text follows. Its speaker pauses mid-sentence, and transcription turned those "
    "pauses into sentence breaks - periods and capital letters in the middle of what is really "
    "one sentence. Repair ONLY the punctuation, sentence boundaries, and the capitalization that "
    "follows from them, so it reads as the sentences actually meant. Change no words: do not "
    "add, remove, or reorder a single word. Reply with the repaired text alone - no preamble, "
    "no quotes.\n\n{text}"
)


def _polish_options():
    return ClaudeAgentOptions(
        tools=[],  # it repairs text; it does not get to look anything up
        model=POLISH_MODEL,
        permission_mode="bypassPermissions",
        setting_sources=[],
    )


def same_words(one, another):
    """Whether two texts carry exactly the same letters and digits in the same order - the
    invariant a punctuation-only repair cannot break."""
    strip = lambda text: re.sub(r"[^a-z0-9]", "", text.casefold())
    return strip(one) == strip(another)


class Polisher:
    """One warm session that repairs a draft's punctuation at submit, inside a hard deadline."""

    def __init__(self, *, session_factory=SdkSession, deadline=POLISH_DEADLINE):
        self._session_factory = session_factory
        self._deadline = deadline
        self._session = None
        self._lock = threading.Lock()

    def warmup(self):
        """Open the session now, at startup, so the first submit pays no cold start."""
        self._ensure_session()

    def polish(self, text):
        """The text with its sentence boundaries repaired - or exactly as given, whenever the
        repair is late, failed, or touched anything beyond punctuation."""
        if not text.strip():
            return text
        outcome = {}
        done = threading.Event()

        def work():
            try:
                outcome["said"] = self._ensure_session().ask(PROMPT.format(text=text))
            except Exception:
                outcome["said"] = None
            finally:
                done.set()

        threading.Thread(target=work, daemon=True).start()
        if not done.wait(self._deadline):
            return text  # late: his words go through as typed rather than holding the turn
        said = (outcome.get("said") or "").strip()
        if not said or not same_words(said, text):
            return text  # the repair ate or added a word; the code refuses it wholesale
        return said

    def _ensure_session(self):
        with self._lock:
            if self._session is None:
                self._session = self._session_factory(_polish_options())
            return self._session

    def close(self):
        with self._lock:
            session, self._session = self._session, None
        if session is not None:
            try:
                session.close()
            except Exception:
                pass  # a session already gone must not block shutdown
