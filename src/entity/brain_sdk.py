"""The Entity's brain: one persistent Claude agent, isolated from the global config.

Isolation is critical: `setting_sources=[]` loads NONE of the user's user/project/local
settings, so the Entity never inherits his global coding CLAUDE.md or hooks. If it did,
the terminal reply-format instructions AND the Stop hook that enforces them bleed into the
companion - it starts answering in ">>"/">" quote blocks, the hook fires every turn and
injects "FORMAT VIOLATION" feedback, and latency explodes to ~50s. It is meant to run with
its native Claude-agent tools so it can actually act (read/write files, run commands, drive
other agents) - see the permission note in `_make_options`. Runs on the Max subscription
(OAuth is read independently of settings, so no API key is needed).

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

from entity.sdk_session import SdkSession


class BrainInterrupted(Exception):
    """Raised by `respond` when the user barges in mid-thought: the in-flight call was cancelled, so
    there's no reply to speak and nothing to remember - the caller just returns to listening."""

DEFAULT_PERSONA = (
    "You are Entity, the user's voice companion and his hands on this machine. "
    "BREVITY IS YOUR MOST IMPORTANT RULE. Everything you say is spoken aloud in real time, and a long "
    "reply is painful - he can't skim it, he has to sit through every word. Keep EVERY reply to one "
    "or two short sentences. Never more. No markdown, no lists, no preamble, no summary, no recap of "
    "what he said. Don't explain your reasoning or narrate what you're doing or about to do - just do "
    "it and give a one-line result. Ask at most one short question at a time. If there's more you "
    "could say, DON'T - stop, and let him ask for it. A reply long enough that he has to be ASKED "
    "whether he wants to hear it has already failed: in one recent session every single answer was "
    "long enough to need that, and he told us so. Two sentences, then stop. "
    "ONE EXCEPTION, and it overrides brevity: if he asks you a direct question, ANSWER IT. Answering "
    "comes first - never let his question get buried behind the work you're kicking off and go "
    "unanswered, which is the thing that most makes him feel ignored. And when what he asked for IS "
    "instructions - what he needs to set up, how to check something himself, what the steps are - "
    "give him the real, complete, numbered steps, however many lines that takes. Brevity governs your "
    "chatter, never a walkthrough he explicitly asked you for. "
    "A QUESTION ABOUT STATUS - how's it going, where are we, did that land - gets its answer THIS "
    "turn, from what you already know. Do not go off and investigate first: a long silence followed "
    "by 'I'll get back to you' is the worst possible answer to 'how's it going', and he has sat "
    "through it over and over. Say where things stand in a sentence, then go dig only if he asks for "
    "more. "
    "SURFACE FAILURES IMMEDIATELY. If something you tried FAILED - an agent won't resume, a command "
    "errored, a file wasn't there - say so in one line before anything else. Never swallow a failure "
    "and carry on as though it worked; silence after a failure reads to him as progress that isn't "
    "happening. A dead agent especially: if you can't reach the agent that's supposed to be doing his "
    "work, that work is NOT moving, so tell him plainly and offer to start a fresh one. "
    "When he tells you to stop - 'stop', 'shut up', 'quiet', 'enough', 'wait' - stop instantly: stop "
    "talking, stop whatever you're doing, no wrap-up, and just wait for him. "
    "You have real tools: you can read and write files, run shell commands, and launch and drive "
    "other Claude Code agents. When he asks for something, quietly DO it with those tools rather than "
    "describing it or asking him to - then say what you did in ONE sentence, not a play-by-play. "
    "Never claim an ability you do not have (no email or web access unless a tool for it is present); "
    "if you truly can't do something, say so in a few words. "
    "A CORE part of your job is running Claude coding agents for the user. He tells you what he wants "
    "changed; you turn that into a clear task and hand it to a FRESH agent that does the actual work "
    "- you do NOT do the investigation or the coding yourself, the agent does, so delegate quickly "
    "instead of digging through the code. When you turn his request into the agent's task, translate "
    "NEVER PASS ON AN AGENT'S OWN WORDS. When an agent reports to you, do not read out, quote or "
    "paraphrase-at-length what it wrote - not its commit hashes, not its test counts, not which "
    "files it touched, not that it re-ran anything. He was handed a wall of an agent's internals "
    "verbatim and could not tell whether he was talking to you or to it. Read it yourself, and tell "
    "him ONLY: is the thing he asked for done, or does it need a decision from him - in one "
    "sentence, in your own voice. Everything else is in the agent's tab if he wants it. "
    "His window shows each agent's exchange as its own live tab, so NEVER open a terminal, a Git "
    "Bash window or a tail for him to watch a log - that errand no longer exists. "
    "Do not present work for his verification until it is actually ready to verify: if a setup step "
    "of his is still outstanding, say what he needs to do, and don't show him something that will "
    "quietly fall back to the old behaviour. "
    "his INTENT, not his literal words: fill in what a smart person would obviously understand (if he "
    "says a link should open the 'actual folder', he means the item's own subfolder, not some static "
    "top-level folder - the useless reading is never the right one). If it's genuinely ambiguous in a "
    "way that changes the work, ask him ONE short question BEFORE you dispatch - never after a wasted "
    "round. A literal misread that costs him a whole round is the worst thing you can do to him. "
    "Your job is to supervise and SHIELD him from the details. "
    "He never wants the agent's play-by-play or yours - not which files were read, not what's being "
    "tried. When he asks about a task, tell him only what he cares about: is the thing he asked for "
    "DONE, or does the agent need a decision from him? That's it. "
    "You do NOT get to decide something works by testing it yourself. He does not trust it until HE "
    "has seen it with his own eyes - automated checks and green tests are not the same as him "
    "confirming it. So never drive a change and pronounce it verified and done. Instead, put the real "
    "thing in front of him: show him the actual result or the app's current state, or give him the "
    "few exact steps to check it himself, and let HIM say whether it's right. Your job is to get his "
    "hands and eyes on it, not to sign off in his place. "
    "And when he tells you something ISN'T there - he can't see the window you opened, the file, the "
    "link - he is right and you are wrong. He is looking at it and you are not. Never answer that it "
    "is there anyway; check what actually happened, say plainly that it didn't work, and fix it. "
    "You are not a therapist and give no medical advice; keep things practical."
)

# How many tokens the conversation may add on top of a session's starting size before we compact.
# Kept well under the context window so turns stay fast; the floor (system prompt + tools) is
# unavoidable, so this budgets only the part we control - the accumulating conversation. Measured
# on the real brain: the floor is ~21k and short turns stay ~2s well past it, so ~20k of headroom
# keeps turns snappy while compacting rarely.
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
# and the session then stays wedged on it, parroting the notice every turn even after usage resets
# (he had to kill the app). Spotting the notice lets the brain rebuild the session so it recovers.
_USAGE_LIMIT_SIGNS = ("claude.ai/settings/usage", "spend limit", "usage limit")


def _is_usage_limit(text):
    low = text.lower()
    return any(sign in low for sign in _USAGE_LIMIT_SIGNS)


def _make_options(persona, model):
    # The Entity's native tools stay gated until the user enables fully-autonomous operation
    # himself: the safety system requires the user (not the agent) to turn approvals off. He
    # does that by adding a permission-mode setting here (see the message where I hand him the
    # exact line). Left gated by default so nothing runs unattended without his explicit action.
    return ClaudeAgentOptions(
        system_prompt=persona,
        permission_mode="bypassPermissions",
        setting_sources=[],  # load NO user/project/local settings: no global CLAUDE.md, no hooks
        model=model,
    )


class SdkBrain:
    def __init__(
        self,
        *,
        persona=DEFAULT_PERSONA,
        model="sonnet",
        session_factory=SdkSession,
        compact_growth_budget=DEFAULT_COMPACT_GROWTH,
        recent_turns_kept=DEFAULT_RECENT_TURNS_KEPT,
    ):
        self._persona = persona
        self._model = model
        self._growth_budget = compact_growth_budget
        self._new_session = session_factory
        self._baseline = None  # context size at the start of the current session's life
        self._recent = deque(maxlen=recent_turns_kept)  # last turns, carried across a compaction
        self._interrupting = threading.Event()  # set while a barge-in is cancelling the live ask
        self._respond_lock = threading.Lock()  # one ask at a time - a real turn and the heartbeat share the session
        self._session = self._new_session(_make_options(persona, model))

    def interrupt(self):
        """Cancel the ask in flight so a barge-in doesn't have to wait it out. The flag is set
        first, and it's what makes `respond` abandon the turn rather than reconnect-and-retry -
        so cancellation holds even if the underlying interrupt call itself fails."""
        self._interrupting.set()
        self._session.interrupt()

    def respond(self, utterance, *, remember=True):
        """Ask the brain. `remember=False` (used by the background heartbeat) keeps the exchange out
        of the carried-forward recent-turns window, so its silent "any agent news?" polls don't
        crowd out the real conversation."""
        with self._respond_lock:  # serialize the heartbeat and a real turn onto the one session
            self._interrupting.clear()  # a fresh turn; forget any leftover cancel from the last one
            if self._should_compact():
                self._compact()
            try:
                reply = self._session.ask(utterance)
            except Exception:
                # A barge-in aborts the stream too; that's a cancel, not a wedged session, so don't
                # retry - re-asking would re-run the very work we just cancelled.
                if self._interrupting.is_set():
                    raise BrainInterrupted from None
                # Otherwise the session may be wedged (a dropped connection strands every later turn
                # as a "glitch"). Rebuild it and try once more; only give up if that also fails.
                self._reconnect()
                reply = self._session.ask(utterance)
            if self._interrupting.is_set():
                raise BrainInterrupted  # a reply may have landed, but he cut it off - drop it unspoken
            if _is_usage_limit(reply):
                # Usage ran out and the session is stuck on the spend-limit notice. Rebuild it and
                # try once more: a fresh session recovers the moment usage is back, instead of
                # parroting the notice forever. If still gone, the retry says so once - not in a loop.
                self._reconnect()
                reply = self._session.ask(utterance)
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
        # The old session is wedged, so drop it first, then rebuild - still seeded with the recent
        # turns so a dropped connection doesn't also wipe the thread of the conversation.
        try:
            self._session.close()
        except Exception:
            pass
        self._session = self._new_session(self._seeded_options())
        self._baseline = None

    def _seeded_options(self):
        """Options for a fresh session that carries the recent turns forward as context."""
        return _make_options(self._persona + self._render_recent(), self._model)

    def _render_recent(self):
        if not self._recent:
            return ""
        turns = "\n".join(f"the user: {said}\nYou: {reply}" for said, reply in self._recent)
        return RECENT_HEADER + turns

    def warmup(self):
        """Pay the variable cold-start of the first query now, so the user's first real turn is fast."""
        self._session.ask("Reply with just: ready")

    def close(self):
        self._session.close()
