"""The cleanup pass between his voice and the brain: pauses out, sentences back.

His natural pauses get transcribed as sentence breaks - "periods and capitalization on next word
even though they aren't natural points to end sentences" - so what reaches the brain reads like
chopped-up fragments and he has to massage the draft by hand. His call on where the fix runs:
"before submitting... hopefully only a second of wait time." So one small fast session, opened at
startup and kept warm, repairs ONLY the punctuation of the submitted draft, and the loop waits on
it briefly - never long, and never at the cost of his words:

- Bounded: past the deadline the raw text goes through as typed. A slow model may not stall a turn.
- Word-safe by CODE, not by trust: every word of the repair must be the raw word in its place -
  identical once punctuation is stripped, or so close a respelling that it can only be the same
  word misheard ("on Maine" for "on main"; he dictates software work, not travel plans). A repair
  that adds, drops, reorders, or outright replaces a word is thrown away whole. The model is ASKED
  to fix only punctuation and plain mishearings; this is what makes it unable to eat a word no
  matter what it answers.
"""

import re
import threading
from difflib import SequenceMatcher

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
    "follows from them, so it reads as the sentences actually meant. Change no words - with one "
    "exception: the speaker is dictating software work, and a word that is plainly the "
    "transcriber mishearing a technical term ('on Maine' for 'on main', 'Jason' for 'JSON') "
    "becomes the word actually said. When in doubt, leave the word alone. Never add, remove, or "
    "reorder a word. Reply with the repaired text alone - no preamble, no quotes.\n\n{text}"
)


def _polish_options():
    return ClaudeAgentOptions(
        tools=[],  # it repairs text; it does not get to look anything up
        model=POLISH_MODEL,
        permission_mode="bypassPermissions",
        setting_sources=[],
    )


def _words(text):
    """The text as bare words: split on whitespace, punctuation and case stripped away."""
    stripped = (re.sub(r"[^a-z0-9]", "", token.casefold()) for token in text.split())
    return [word for word in stripped if word]


def word_safe(raw, repaired):
    """Whether every word of the repair is recognizably the raw word in its place: identical, or
    so close a respelling that it can only be the same word misheard ("Maine" for "main").
    Adding, dropping, reordering, or outright replacing a word fails - a repair may fix his
    words, never take them."""
    ours, its = _words(raw), _words(repaired)
    if len(ours) != len(its):
        return False
    return all(
        mine == theirs or SequenceMatcher(None, mine, theirs).ratio() >= 0.6
        for mine, theirs in zip(ours, its)
    )


class Polisher:
    """One warm session that repairs a draft's punctuation at submit, inside a hard deadline."""

    def __init__(self, *, session_factory=SdkSession, deadline=POLISH_DEADLINE):
        self._session_factory = session_factory
        self._deadline = deadline
        self._session = None
        self._lock = threading.Lock()

    def warmup(self):
        """Open the session AND run one tiny repair now, at startup. Opening alone was not warm:
        the session's first inference still took longer than the deadline, so the first real
        submit of a session went through unrepaired - the chopped Highdeas ask that cost a whole
        misdispatched agent. The model must have answered once before it is fast."""
        try:
            self._ensure_session().ask(PROMPT.format(text="warm up. Ready to go."))
        except Exception:
            pass  # a failed warmup costs only the first submit's repair, never the startup

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
        if not said or not word_safe(text, said):
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
