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

import queue
import re
import threading
from difflib import SequenceMatcher

from claude_agent_sdk import ClaudeAgentOptions

from entity.models import FAMILIES
from entity.sdk_session import SdkSession

POLISH_MODEL = FAMILIES["haiku"]  # a punctuation pass: the smallest, fastest tier there is

_PRECOOK = object()  # the asker-queue token that means "repair the newest draft-so-far"

# How long the submit will wait before letting the raw text through. His acceptance was
# "hopefully only a second" - but a long dictation takes the model longer to re-say, and his
# seventy-word feature request ran out the old flat three seconds and reached the brain as raw
# chop. Measured warm on that very text: 4-10 seconds. So the wait scales with what there is to
# repair, sized to those measurements, capped so a hung model still never holds a turn hostage.
def polish_deadline(text):
    return min(20.0, 6.0 + len(text.split()) / 10.0)

PROMPT = (
    "Dictated text follows. Its speaker pauses mid-sentence, and transcription turned those "
    "pauses into sentence breaks - periods and capital letters in the middle of what is really "
    "one sentence. Repair ONLY the punctuation, sentence boundaries, and the capitalization that "
    "follows from them, so it reads as the sentences actually meant. Change no words - with one "
    "exception: the speaker is dictating software work, and a word that is plainly the "
    "transcriber mishearing a technical term ('on Maine' for 'on main', 'Jason' for 'JSON') "
    "becomes the word actually said.{terms} When in doubt, leave the word alone. Never add, "
    "remove, or reorder a word. Reply with the repaired text alone - no preamble, no quotes. "
    "For example, given: 'we should Ship it. Tomorrow morning? and also the Icon' reply: "
    "'we should ship it tomorrow morning, and also the icon.'\n\n{text}"
)

# The speaker's own vocabulary rides into the prompt when the polisher was given any: the
# transcriber writes "ideas" and "Notes nook" because it cannot know Highdeas and Notesnook -
# and neither can the repair model, unless the names are in front of it.
TERMS_NOTE = " Their domain terms include: {listed}."


def _prompt_for(text, terms):
    listed = ", ".join(sorted(terms, key=str.casefold)) if terms else ""
    return PROMPT.format(text=text, terms=TERMS_NOTE.format(listed=listed) if listed else "")


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
    """Whether every word of the repair is recognizably the raw words in their places: aligned
    stretch by aligned stretch, a repair may respell what was misheard ("Maine" for "main") and
    may join or split the letters of a coined term ("Notes nook" for "Notesnook") - but a
    stretch of his words with no close counterpart in the repair is his speech being eaten, and
    the whole repair is refused. The strict same-count rule refused every repair that contained
    one join, which in practice meant the worst dictations - the ones leaning on coined names -
    went through raw."""
    ours, its = _words(raw), _words(repaired)
    for verb, our_from, our_to, its_from, its_to in SequenceMatcher(None, ours, its).get_opcodes():
        if verb == "equal":
            continue
        if verb != "replace":
            return False  # words eaten or invented outright
        mine = "".join(ours[our_from:our_to])
        theirs = "".join(its[its_from:its_to])
        if SequenceMatcher(None, mine, theirs).ratio() < 0.6:
            return False  # not a mishearing of the same stretch - different words entirely
    return True


class Polisher:
    """One warm session that repairs a draft's punctuation at submit, inside a hard deadline."""

    def __init__(self, *, session_factory=SdkSession, deadline=None, terms=None):
        self._session_factory = session_factory
        # A number pins every wait (the tests' handle); None means the scaled default - longer
        # drafts get longer, capped.
        self._deadline = deadline
        self._terms = terms  # callable -> the speaker's domain terms, read live per repair
        self._session = None
        self._lock = threading.Lock()
        # EVERY exchange with the session runs on one thread of its own, warmup included. Asked
        # from whatever thread happened to call - startup's thread for the warmup, the mic
        # pump's for every real repair - the very same session answered the warmup and then
        # answered the pump with EMPTY text every time, so in the running app no dictation was
        # ever repaired at all. One thread, one behavior.
        self._asks = queue.SimpleQueue()
        self._asker = None
        self._precook_want = None   # the newest draft-so-far asked for
        self._precooked = None      # (source draft, its finished repair)

    def _serve(self):
        while True:
            prompt, box, done = self._asks.get()
            if prompt is None:
                return  # the polisher closed; the thread winds down with it
            if prompt is _PRECOOK:
                self._precook_now()
                continue
            try:
                box["said"] = self._ensure_session().ask(prompt)
            except Exception:
                box["said"] = None
            finally:
                done.set()

    def precook(self, draft):
        """Start repairing the draft AS IT GROWS - "ideally something is already working in the
        background while I'm speaking", his words when this feature was asked for. Each pause's
        chunk re-queues the whole draft-so-far; the asker repairs the newest version on its own
        time, no deadline, and submit() then finds most of the work already done."""
        if draft.strip():
            self._start_asker()
            self._precook_want = draft  # newest wins; the asker reads it when it gets there
            self._asks.put((_PRECOOK, None, None))

    def _precook_now(self):
        wanted = self._precook_want
        if not wanted or (self._precooked and self._precooked[0] == wanted):
            return
        try:
            said = self._ensure_session().ask(_prompt_for(wanted, self._live_terms())).strip()
        except Exception:
            return
        if wanted is not self._precook_want and self._precook_want != wanted:
            # The draft grew while this repaired; the queue already holds a newer token, and a
            # stale repair is still a fine PREFIX repair, so it is kept unless a newer one lands.
            pass
        if said and word_safe(wanted, said):
            self._precooked = (wanted, said)

    def _start_asker(self):
        with self._lock:
            if self._asker is None:
                self._asker = threading.Thread(target=self._serve, daemon=True)
                self._asker.start()

    def _ask(self, prompt, allowed):
        self._start_asker()
        box, done = {}, threading.Event()
        self._asks.put((prompt, box, done))
        if not done.wait(allowed):
            return None  # late; the caller lets the words through as typed
        return box.get("said")

    def _live_terms(self):
        try:
            return tuple(self._terms()) if self._terms is not None else ()
        except Exception:
            return ()  # the vocabulary must never break a repair

    def warmup(self):
        """Queue one tiny repair now, at startup, on the same thread every later repair uses -
        and return at once: the cold start runs twenty seconds and more, and blocking the boot
        on it bought nothing (a repair that arrives while the session is still cold goes through
        raw exactly once, bounded by its own deadline). The warmup text is deliberately BROKEN:
        the session keeps its own history, and a warmup that needed no repair taught it by
        example to answer text back unchanged - after which a seventy-word dictation full of
        chop came back untouched, twice in a row. Its first exchange must demonstrate the job."""
        self._start_asker()
        self._asks.put((_prompt_for("warming Up. the session with chopped. Punctuation to repair",
                                    self._live_terms()), {}, threading.Event()))

    def polish(self, text):
        """The text with its sentence boundaries repaired - or exactly as given, whenever the
        repair is late, failed, or touched anything beyond punctuation.

        The background repair (see `precook`) usually means the work is already done: the whole
        draft, or all but its newest tail, comes straight from the cache and only the tail waits
        on the model - because a full repair of a long dictation runs four to fifteen seconds,
        and a submit that blocked that long lost the race every time it mattered."""
        if not text.strip():
            return text
        cooked = self._precooked
        if cooked and text == cooked[0]:
            return cooked[1]
        head, tail = "", text
        if cooked and text.startswith(cooked[0]):
            head, tail = cooked[1], text[len(cooked[0]):]
        allowed = self._deadline if self._deadline is not None else polish_deadline(tail)
        said = (self._ask(_prompt_for(tail, self._live_terms()), allowed) or "").strip()
        if not said or not word_safe(tail, said):
            said = tail.strip()  # the tail goes as spoken; the repaired head still counts
        return f"{head} {said}".strip() if head else said

    def _ensure_session(self):
        with self._lock:
            if self._session is None:
                self._session = self._session_factory(_polish_options())
            return self._session

    def close(self):
        self._asks.put((None, None, None))  # the asker thread winds down with the session
        with self._lock:
            session, self._session = self._session, None
        if session is not None:
            try:
                session.close()
            except Exception:
                pass  # a session already gone must not block shutdown
