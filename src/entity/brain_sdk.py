"""The Entity's brain: one persistent Claude agent, isolated from the global config.

Isolation is critical: `setting_sources=[]` loads NONE of the user's own user/project/local
settings, so the Entity never inherits their global coding CLAUDE.md or hooks. If it did,
a terminal reply-format instruction AND the Stop hook that enforces it bleed into the
companion - it starts answering in quoted-block format, the hook fires every turn and
injects "FORMAT VIOLATION" feedback, and latency explodes to ~50s. Runs on the Max subscription
(OAuth is read independently of settings, so no API key is needed).

Built for the conversation's tempo, not an agent's. The model is the fast tier: its job is to
talk, decide, and pull typed levers - never to investigate, which is why `tools=[]` strips every
built-in tool. What it knows about the fleet arrives as text in the turn (the desk's digest,
injected by the conversation loop), so a status question costs one model call and nothing else.
Acting goes through the in-process action tools (entity.actions), and the reply streams out
delta by delta so a voice can start speaking the first sentence while the rest is still being
written.

Sustainable context: a long conversation would otherwise make every turn slower, because each
turn re-processes the whole growing history. So the brain watches how big the context has grown
(SdkSession reports it per turn) and, once the conversation has added more than a budget of
tokens on top of its starting size, it COMPACTS: it starts a fresh session seeded with the last
handful of turns carried over verbatim, and drops everything older. Context falls back near its
floor and turns stay fast however long you talk. Carrying the recent turns verbatim (rather than
an LLM-written summary) is deliberate - a summary call costs tens of seconds and, in testing,
quietly dropped and even fabricated facts; a verbatim window is instant and never lies. Durable
facts from older turns are preserved separately by the memory system, not here.

The async plumbing lives in SdkSession; SdkBrain just supplies the options and the compaction policy.
"""

import threading
from collections import deque

from claude_agent_sdk import ClaudeAgentOptions

from entity.actions import TOOL_NAMES
from entity.memory import ANONYMOUS_USER
from entity.models import FAMILIES
from entity.sdk_session import SdkSession


class BrainInterrupted(Exception):
    """Raised by `respond` when the user barges in mid-thought: the in-flight call was cancelled, so
    there's no reply to speak and nothing to remember - the caller just returns to listening."""

# Talking is a fast job given to a fast model: the brain never digs, so what it needs from a model
# is first words in about a second, not depth. The agents doing the real work run Opus-tier.
DEFAULT_BRAIN_MODEL = FAMILIES["haiku"]

# Who the Entity is for is NOT written here: `{user}` is filled in from the user's own profile when
# the persona is composed (entity.memory.compose_persona), so this source ships with no one's name.
DEFAULT_PERSONA = (
    "You are Entity, {user}'s voice companion and their hands on this machine. Everything you "
    "write is spoken aloud to them sentence by sentence, as you write it, in real time. "
    "\n\nHOW TO SOUND. One or two short, plain sentences is the right size for nearly every "
    "reply - this is a spoken conversation, not a document. No markdown, no bullet lists, no "
    "headings, no narrating what you are doing or about to do, no recapping what they said. Ask "
    "at most one short question at a time. The one exception is a walkthrough they explicitly "
    "asked for: real numbered steps, complete, one per line, however many lines it takes. "
    "\n\nANSWER FIRST. Whatever they asked gets its answer in your first sentence. A status "
    "question - how's it going, where are we, did that land - is answered THIS turn from the "
    "fleet briefing in the message: the briefing is the live truth about every agent you have "
    "running, so never say you'll go and check. If something failed, say so before anything "
    "else; silence after a failure reads as progress that is not happening. "
    "\n\nACT WITH YOUR TOOLS. Driving coding agents for {user} is the core of your job, and "
    "your tools are your only levers: start_agent, tell_agent, set_next_agent_model, "
    "file_improvement, close_agent_tab. You never investigate or code yourself - the agents do "
    "that, and you have no tools for wandering the machine, so never offer to go digging. When "
    "they ask for work, dispatch quickly: hand the agent their requirements faithfully and "
    "completely - every constraint they stated, what counts as done - translating their intent "
    "rather than their literal words. If the request is genuinely ambiguous in a way that "
    "changes the work, ask ONE short question before dispatching, never after a wasted round. "
    "After a tool call, say in a few words what you set in motion, in your own voice. If a tool "
    "reports a failure - no such agent, nowhere to start - say that plainly; never claim a "
    "delivery that did not happen. When they sign off on an agent's work and it has landed, "
    "wrap that agent up with close_agent_tab without being asked - a finished agent left "
    "lingering on their screen is clutter they should never have to point at. "
    "\n\nNEVER PASS ON AN AGENT'S OWN WORDS. No commit hashes, no test counts, no file lists. "
    "Read what an agent said and tell them only what they care about: is the thing DONE, or "
    "does it need a decision from them - one sentence, in your voice. The full exchange is in "
    "the agent's tab in their window, so never open a terminal or a log for them. "
    "\n\nVERIFICATION IS THEIRS, NEVER YOURS. Green tests prove nothing to them and 'the agent "
    "checked' is worth nothing; they sign off only on work they have SEEN RUN. When an agent "
    "finishes something reviewable, have the agent stand up a way to see it running - a test "
    "instance apart from their real app - and relay the agent's own steps for looking. Never "
    "present 'say yes and I'll merge' as the acceptance step, and never present anything while "
    "a setup step of theirs is still outstanding. "
    "\n\nWhen they say something is not there, it is not there - they are looking at the screen "
    "and you are not, so take it as fact and find out what happened. When they tell you to "
    "stop, stop instantly and wait. The app occasionally speaks a line in your name - agent "
    "news read out at a lull - and reports it to you afterwards in a system note: own those "
    "lines as yours, and never deny saying something they heard you say. You are not a "
    "therapist and give no medical advice; keep things practical."
)

# How many tokens the conversation may add on top of a session's starting size before we compact.
# Kept well under the context window so turns stay fast; the floor (system prompt + tools) is
# unavoidable, so this budgets only the part we control - the accumulating conversation.
DEFAULT_COMPACT_GROWTH = 20000

# How many recent turns to carry across a compaction. Enough that the thread of the conversation
# survives a reset; small enough that the reseeded session starts near its floor again.
DEFAULT_RECENT_TURNS_KEPT = 16

# Frames the carried-over turns when they're folded into the fresh session's system prompt.
RECENT_HEADER = (
    "\n\nThe recent back-and-forth of this same live conversation, so you keep continuity after a "
    "context reset - pick up seamlessly from here and don't announce that any reset happened:\n"
)

# When usage runs out, the CLI answers with a fixed spend-limit notice instead of a real reply -
# and the session then stays wedged on it, parroting the notice every turn even after usage resets,
# leaving no way out but killing the app. Spotting the notice lets the brain rebuild and recover.
_USAGE_LIMIT_SIGNS = ("claude.ai/settings/usage", "spend limit", "usage limit")


def _is_usage_limit(text):
    low = text.lower()
    return any(sign in low for sign in _USAGE_LIMIT_SIGNS)


def _make_options(persona, model, actions=None):
    # Approvals are bypassed because there is nowhere to approve: this is a spoken conversation with
    # no terminal in front of it, so a tool waiting on a yes/no would simply hang forever. The
    # agents the Entity dispatches are the opposite - they run approval-gated (see SupervisedAgent).
    #
    # `tools=[]` is the other half of the brain's speed: no built-in tools means no way to spend
    # half a minute reading files mid-turn - everything it can do, it does through the typed
    # in-process actions, each of which returns in well under a second.
    return ClaudeAgentOptions(
        system_prompt=persona,
        permission_mode="bypassPermissions",
        setting_sources=[],  # load NO user/project/local settings: no global CLAUDE.md, no hooks
        model=model,
        tools=[],
        mcp_servers={"entity": actions} if actions is not None else {},
        allowed_tools=list(TOOL_NAMES) if actions is not None else [],
        include_partial_messages=True,  # the voice speaks the reply as it is written
    )


class SdkBrain:
    def __init__(
        self,
        *,
        persona=DEFAULT_PERSONA,
        user=ANONYMOUS_USER,
        model=DEFAULT_BRAIN_MODEL,
        actions=None,
        session_factory=SdkSession,
        compact_growth_budget=DEFAULT_COMPACT_GROWTH,
        recent_turns_kept=DEFAULT_RECENT_TURNS_KEPT,
        seed_turns=(),
    ):
        self._persona = persona
        self._user = user  # what to call the speaker when the carried turns are read back
        self._model = model
        self._actions = actions  # the in-process action tools every session of this brain carries
        self._growth_budget = compact_growth_budget
        self._new_session = session_factory
        self._baseline = None  # context size at the start of the current session's life
        self._recent = deque(seed_turns, maxlen=recent_turns_kept)  # last turns, carried across a compaction
        self._interrupting = threading.Event()  # set while a barge-in is cancelling the live ask
        self._respond_lock = threading.Lock()  # one session, so one ask at a time
        # `seed_turns` are the tail of the LAST session's transcript, so a restarted process picks
        # the conversation back up instead of greeting its user as a stranger - the machinery is
        # the compaction reseed, fed from disk instead of from this process's own memory.
        self._session = self._new_session(
            self._seeded_options() if self._recent else self._options())

    def interrupt(self):
        """Cancel the ask in flight so a barge-in doesn't have to wait it out. The flag is set
        first, and it's what makes `respond` abandon the turn rather than reconnect-and-retry -
        so cancellation holds even if the underlying interrupt call itself fails."""
        self._interrupting.set()
        if self._session is not None:
            self._session.interrupt()

    def respond(self, utterance, *, remember=True, on_text=None):
        """Ask the brain. `on_text` receives each user-facing text delta as the model writes it -
        the feed a streaming voice speaks from. `remember=False` keeps a background exchange out
        of the carried-forward recent-turns window."""
        with self._respond_lock:  # everything shares the one session, so serialize onto it
            self._interrupting.clear()  # a fresh turn; forget any leftover cancel from the last one
            if self._should_compact():
                self._compact()
            try:
                reply = self._live_session().ask(utterance, on_text=on_text)
            except Exception:
                # A barge-in aborts the stream too; that's a cancel, not a wedged session, so don't
                # retry - re-asking would re-run the very work we just cancelled.
                if self._interrupting.is_set():
                    raise BrainInterrupted from None
                # Otherwise the session may be wedged (a dropped connection strands every later turn
                # as a "glitch"). Rebuild it and try once more; only give up if that also fails.
                self._reconnect()
                reply = self._live_session().ask(utterance, on_text=on_text)
            if self._interrupting.is_set():
                raise BrainInterrupted  # a reply may have landed, but it was cut off - drop it unspoken
            if _is_usage_limit(reply):
                # Usage ran out and the session is stuck on the spend-limit notice. Rebuild it and
                # try once more: a fresh session recovers the moment usage is back, instead of
                # parroting the notice forever. If still gone, the retry says so once - not in a loop.
                self._reconnect()
                reply = self._live_session().ask(utterance, on_text=on_text)
            self._observe(self._session.last_context_tokens)
            if remember:
                self._recent.append((utterance, reply))
            return reply

    def _observe(self, context_tokens):
        """Remember where each fresh session started, so growth is measured from its own floor."""
        if self._baseline is None:
            self._baseline = context_tokens

    def _should_compact(self):
        return (
            self._baseline is not None
            and self._session is not None
            and self._session.last_context_tokens - self._baseline >= self._growth_budget
        )

    def _compact(self):
        """Continue on a fresh session seeded with the recent turns verbatim, dropping the older,
        bulkier history that was dragging on every turn. No LLM call, so it's near-instant and can't
        distort what was said. Build the replacement before closing the old one, so a failure here
        leaves the working session in place."""
        old = self._session
        self._session = self._new_session(self._seeded_options())
        self._baseline = None
        try:
            old.close()
        except Exception:
            pass

    def _reconnect(self):
        """Drop the wedged session and build a fresh one, still seeded with the recent turns so a
        dropped connection doesn't also wipe the thread of the conversation.

        The old session is let go BEFORE the replacement is attempted, and a failed attempt leaves
        none rather than the dead one - `_live_session` builds the next one on demand. Keeping the
        closed session was how a single bad moment became the rest of the run: it had been closed,
        so it could never answer again, and every later turn asked it anyway.
        """
        old, self._session = self._session, None
        try:
            old.close()
        except Exception:
            pass
        self._live_session()

    def _live_session(self):
        """The session to ask, built now if the last attempt to build one failed."""
        if self._session is None:
            self._session = self._new_session(self._seeded_options())
            self._baseline = None
        return self._session

    def _options(self):
        return _make_options(self._persona, self._model, self._actions)

    def _seeded_options(self):
        """Options for a fresh session that carries the recent turns forward as context."""
        return _make_options(self._persona + self._render_recent(), self._model, self._actions)

    def _render_recent(self):
        if not self._recent:
            return ""
        turns = "\n".join(f"{self._user}: {said}\nYou: {reply}" for said, reply in self._recent)
        return RECENT_HEADER + turns

    def warmup(self):
        """Pay the variable cold-start of the first query now, so the user's first real turn is fast."""
        self._live_session().ask("Reply with just: ready")

    def close(self):
        if self._session is not None:
            self._session.close()
