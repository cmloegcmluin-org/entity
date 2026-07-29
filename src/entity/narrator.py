"""Agent news, spoken by the same voice the user talks to.

What an agent produced used to reach the user as a concatenation - agent name, colon, first
sentence, capped - and he called it what it was: "I don't appreciate how you're speaking to me in
code." A notice is a label; a voice is someone telling you a thing. So each event now takes one
trip through the brain - which reads the agent's report and composes the one or two sentences the
user actually hears, in its own words, remembered as part of the conversation - and THAT goes to
the outbox for the lull. The relay's plain notice survives only as the fallback when the brain
cannot answer, because news must never die with a wedged session.
"""

import re
import threading

from entity.relay import notice

# The routing word, wherever it leads the reply. "Handled - <news>" reached the user verbatim and
# he had to ask what it referred to ("The word 'handled' doesn't appear to refer to anything...").
# Alone it means silence; leading real news it is stripped, because it is protocol, never speech.
_HANDLED_LEAD = re.compile(r"(?i)^handled\b[\s\-–—:,.!]*")

# How long one narration may sit inside the brain before the plain notice ships instead. The brain
# serializes its turns, so this must cover a few queued narrations - but not much more: the night
# everything after 04:43 went unspoken, one hung narration held the lock and the agent's merge
# report and the quiet warning both queued behind it until the app closed and they died. News must
# never die with a wedged session - that is this module's whole reason to exist.
NARRATE_DEADLINE = 120.0

# What the brain is asked, by kind of event. Each is a system-originated turn: the brain answers
# it the way it answers anything - and because it composed the words, it remembers saying them.
PROMPTS = {
    "finished": (
        "[Agent event, from the app - not the user speaking. Your agent {agent} just finished a "
        "turn and reported:\n{report}\n\nTell the user, in your own one or two short sentences: "
        "is the thing they wanted DONE, or does it need a decision or a step from them? If it is "
        "ready to look at, give them the agent's own see-it-running steps - where to click and "
        "what to watch happen. 'Run the tests' is never their verification; if the agent stood "
        "nothing up for their eyes, say the review isn't ready and tell the agent to stand one "
        "up. Until they have signed off AND a merge is reported, never say 'fixed', 'done', or "
        "anything that reads as already landed - the only frame before sign-off is 'ready for "
        "your eyes', with the steps ('The text selection bug is fixed' reached them about work "
        "they had not even seen, and they called it out). Never relay the report's internals - "
        "no commit hashes, no test counts, no branch "
        "names, no file lists. And if the report is only the agent pausing mid-task - narrating "
        "a step, asking leave to continue, nothing done and nothing the user must decide - do "
        "not interrupt them at all: use tell_agent to tell it to continue, and answer with the "
        "single word: handled - the whole reply, never the first word of a longer one, and never "
        "a word you say TO the user. If it is stuck on something TECHNICAL - it needs feedback or "
        "a decision you can't confidently give - use ask_foreman instead of guessing or bothering "
        "the user; only their own calls (preference, scope, sign-off) go to them.]"
    ),
    # A finished turn from an agent that was landing already-approved work: the loop's last leg.
    # Everything after the user's sign-off is mechanical, so the wrap-up is commanded here, not
    # handed back to the user as a chore.
    "landing": (
        "[Agent event, from the app - not the user speaking. Your agent {agent} was landing work "
        "the user had already approved, and reported:\n{report}\n\nIf the report says the work "
        "merged, call close_agent_tab for {agent} right now - the wrap-up is yours to do, not "
        "theirs - then tell the user in one short sentence that it is in and wrapped up. If it "
        "did not land, tell them in one sentence what is stuck. Never relay the report's "
        "internals - no commit hashes, no test counts, no branch names, no file lists.]"
    ),
    # A restart found an agent holding work it had already presented and never got a verdict on.
    # Nothing re-engages an idle agent, so without this the review simply stopped existing: he
    # rejected a round, the agent fixed and re-presented into a closed app, and the next launch
    # said nothing - "I never heard back again."
    "pending": (
        "[Agent event, from the app - not the user speaking. You have just started up, and your "
        "agent {agent} is holding finished work it presented for the user's eyes that never got "
        "a verdict. The steps it gave for seeing it run:\n{report}\n\nRemind them in your own "
        "one or two short sentences that it is waiting on their yes or no, and give them those "
        "steps - where to click and what to watch happen. Never relay internals - no commit "
        "hashes, no test counts, no branch names, no file lists.]"
    ),
    "died": (
        "[Agent event, from the app - not the user speaking. Your agent {agent} DIED mid-task: "
        "{report}\n\nTell the user plainly in one short sentence that it died and what you "
        "propose - their work is not moving until they decide.]"
    ),
    "wrote": (
        "[Agent event, from the app - not the user speaking. Your agent {agent} wrote this to "
        "its inbox for the user:\n{report}\n\nPass on what matters in your own one or two short "
        "sentences: what it needs, or what is ready.]"
    ),
    "errand": (
        "[Agent event, from the app - not the user speaking. The quiet errand hand finished a "
        "small local chore and reported: {report}\n\nTell the user in ONE short sentence that "
        "it's done, or exactly what stopped it - no internals, no tool talk.]"
    ),
    "quiet": (
        "[Agent event, from the app - not the user speaking. Your agent {agent} has {report} - "
        "it may be hung. Use ask_foreman to have the senior model read its log and prod it, and "
        "tell the user in one short sentence what you've set in motion - or, if you already "
        "know it needs THEM, say that instead.]"
    ),
    # The memory inbox: raised only in downtime (see review.MemoryNudger), one fact at a time,
    # because he wants the store worked to zero - "this list is an inbox, and I'm an inbox-0
    # kind of guy" - without ever being interrupted for it.
    "memory": (
        "[Quiet-moment housekeeping, from the app - not the user speaking. One memory from your "
        "store awaits his review:\n{report}\n\nRead it back in your own short sentence and ask "
        "what he wants done with it - kept, dropped (forget_memory), or made a standing "
        "instruction (update_persona, then forget_memory so it leaves the inbox). One memory, "
        "one short question, never a list.]"
    ),
}


class Narrator:
    """Turns one agent event into one brain-composed interjection, off-thread, never lost."""

    def __init__(self, brain, outbox, stage_of=None, deadline=NARRATE_DEADLINE):
        self._brain = brain
        self._outbox = outbox
        self._deadline = deadline
        # Where the agent's work stands in the delivery loop (the desk's delivery_stage) - the
        # same finished turn is presentation news while building, wrap-up news while landing.
        self._stage_of = stage_of or (lambda agent: None)

    def tell(self, kind, agent, report):
        """Narrate one event. Returns at once; the composed line lands in the outbox when ready.

        Off-thread because the brain serializes its turns: an event landing mid-reply waits its
        turn on the brain's own lock, and nothing here may hold up the desk that emitted it."""
        threading.Thread(target=self._narrate, args=(kind, agent, report), daemon=True).start()

    def _narrate(self, kind, agent, report):
        if kind == "finished" and self._stage_of(agent) == "landing":
            kind = "landing"
        prompt = PROMPTS.get(kind, PROMPTS["finished"]).format(agent=agent, report=report)
        # One claim on delivering this event: whichever of the two threads takes it speaks, the
        # other stays silent. Without it, a reply landing just as the deadline runs out would be
        # spoken AND covered by the notice - the same news twice.
        claim = threading.Lock()
        claimed = []
        composed = threading.Event()

        def take():
            with claim:
                if claimed:
                    return False
                claimed.append(True)
                return True

        def compose():
            try:
                said = self._brain.respond(prompt, remember=True)
            except Exception:
                said = ""
            else:
                stripped = _HANDLED_LEAD.sub("", said.strip())
                if said.strip() and not stripped:
                    # The brain kicked the agent onward itself; there is no news to deliver.
                    # Claimed, so a timed-out waiter doesn't ship a notice about it either way -
                    # composing finished in time or it didn't, and the brain chose silence.
                    take()
                    composed.set()
                    return
                said = stripped
            if take():
                if said.strip():
                    self._outbox.push(said.strip(), about=agent, composed=True)
                else:
                    # The brain could not answer; the capped plain notice still carries the news,
                    # marked app-authored so the ledger reads it back to the brain next turn.
                    self._outbox.push(notice(agent, report), about=agent)
            composed.set()

        threading.Thread(target=compose, daemon=True).start()
        if not composed.wait(self._deadline) and take():
            # The brain has sat on this past the deadline - wedged, or buried under a queue that
            # will outlive the user's patience. The notice ships now; the late answer is dropped.
            self._outbox.push(notice(agent, report), about=agent)
