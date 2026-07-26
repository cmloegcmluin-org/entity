import inspect
import re
import sys
import threading
import time
from dataclasses import dataclass

from entity.console import Console
from entity.links import as_spoken
from entity.phrases import canonical as _canonical
from entity.phrases import ends_with_command as _ends_with_command
from entity.phrases import wakes as _wakes
from entity.waiting import chosen, roll_call

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
DEFAULT_FAREWELL_REPLY = "Be seeing you."
# What they hear when the brain call fails, with `{cause}` filled in. The cause is IN the sentence
# because it used to go to stderr - and the windowed run is launched by pythonw, which has no stderr
# at all, so the one line that could explain the failure went nowhere. Every failure then sounded
# like the same momentary hiccup, including the ones that had wedged the session permanently: "it
# has never said that and recovered". Sounding human about a fault you can't explain is worth less
# than the fault.
DEFAULT_ERROR_REPLY = "Something's broken in my head: {cause}"
# They ended a turn ("over") but said nothing in it. Rather than ignore them - which just makes them
# repeat "over" wondering if they were heard - acknowledge that the turn registered and invite them on.
DEFAULT_EMPTY_TURN_REPLY = "Go ahead."
DEFAULT_SUSPEND_REPLY = "Resting. Say 'hey Entity' when you want me back."
DEFAULT_RESUME_REPLY = "Back with you."

# Said back to the brain on the turn AFTER anything was spoken in its name that it did not write -
# an agent's notice, a roll call, a canned confirmation.
#
# The user hears ONE Entity. The brain only ever knew the half of it that it composed, so they could
# quote a line at it and be told, truthfully, "I have no record of typing that myself" - which from
# where they sit is a thing that said something and then denied saying it. Their words: "a basic
# principle of two people having a conversation is that each person is aware of the things that
# they've said. If what I consider to be one Entity is actually a bunch of disconnected fakers who
# aren't aware of each other, then the flimsy occasional illusion of you being a coherent Entity is
# worse than useless."
UNWRITTEN_NOTICE = (
    "[System note, not from the user: since your last reply, these lines were spoken to them in "
    "YOUR name by the app rather than composed by you. They experienced every one of them as you "
    "talking, and may refer to them as things you said - so own them and answer accordingly, and "
    "never tell them you have no record of saying something they heard you say:\n{lines}]\n\n"
)

# The live state of the fleet, put in front of the brain at the top of every turn by code. This is
# what lets "how's it going" be answered in the breath it was asked: the old brain went off to read
# the roster file with its own tools - thirty seconds to fifteen minutes of dead air for state the
# process already held.
BRIEFING_NOTICE = (
    "[Fleet briefing, from the app - the live state of your agents as of this turn:\n{briefing}]\n\n"
)

# While the brain thinks, re-check this often for a barge-in, so cutting a slow think off feels
# instant rather than waiting out the next check-in.
DEFAULT_INTERRUPT_POLL = 0.05
# After telling the brain to cancel, wait up to this long for the call to actually unwind before
# moving on - so the loop never starts a second brain call overlapping a half-cancelled one.
DEFAULT_CANCEL_WAIT = 10.0

# After a reply, wait this long before listening again, so they get a beat to read it rather than the
# mic reopening the instant the voice stops. 0 disables (default; the app turns it on for voice runs).
DEFAULT_READ_PAUSE = 0.0

# With no word from the user for this long, they are off doing something else - and news breaking
# in "out of nowhere" is a jolt. Dormant, the news is OFFERED instead of read: one line naming who
# it is about, and the content waits until they engage, so they decide when to stop and listen.
DEFAULT_DORMANT_AFTER = 180.0

# How the offer is worded. App-authored (the ledger reads it back to the brain), because it must
# be sayable even while the brain is mid-something-else.
UPDATE_OFFER = "I've got an update on {what} when you're ready."

# A bare go-ahead releases a held update. Exact matches only: "okay" mid-sentence is them talking,
# not them asking for the news.
_GO_AHEADS = frozenset((
    "ok", "okay", "yes", "yeah", "yep", "sure", "ready", "go ahead", "go for it", "alright",
    "hit me", "im ready", "i m ready", "lets hear it", "let s hear it", "go",
))


class _ThinkInterrupted(Exception):
    """Internal signal that a barge-in cancelled the brain call - the turn is abandoned and the
    loop goes straight back to listening, with no reply and no error spoken."""


def _cause(exc, depth=3):
    """What broke, and what broke underneath that.

    A library's top exception is often its GUESS at the cause: the agent SDK raises "Claude Code not
    found at <path>" for ANY FileNotFoundError while spawning, so it blamed the CLI while the CLI sat
    there untouched - and that guess was the whole of what there was to go on. Python chains the real
    error underneath, and it knows which file. Bounded, because past the first few links a chain is
    library plumbing rather than anything about what went wrong.
    """
    links, seen = [], set()
    while exc is not None and len(links) < depth and id(exc) not in seen:
        seen.add(id(exc))
        detail = f"{type(exc).__name__}: {exc}"
        missing = getattr(exc, "filename", None)
        if missing and str(missing) not in detail:
            detail += f" ({missing})"
        links.append(detail)
        exc = exc.__cause__ or exc.__context__
    return ", caused by ".join(links)


def _newest_per_agent(waiting):
    """Undelivered news about an agent, superseded by newer news about the same agent.

    Every turn-end while the user was away queued its own narration, and the roll call then read
    the same name four times - a list with no choice in it. The newest sentence about an agent
    already says where things stand; the ones they never heard are history. News with no agent
    on it (about=None) is never collapsed - those are not updates on one thing."""
    keep = []
    for place, item in enumerate(waiting):
        about = getattr(item, "about", None)
        if about is not None and any(
            getattr(newer, "about", None) == about for newer in waiting[place + 1:]
        ):
            continue
        keep.append(item)
    return keep


def _accepts_streaming(brain):
    """Whether this brain's respond() can hand text out as it is written (an `on_text` keyword).

    Checked once rather than per call, so a brain fake with the plain signature runs the plain
    path instead of blowing up mid-turn."""
    try:
        return "on_text" in inspect.signature(brain.respond).parameters
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True)
class Turn:
    heard: str
    said: str
    farewell: bool = False
    error: bool = False


class Conversation:
    """Ties speech-to-text, a brain, and text-to-speech into a listen -> think -> speak loop.

    The shape of a turn is: their words go to the brain with the fleet briefing in front of them,
    and the reply is SPOKEN AS IT IS WRITTEN - each sentence sounding while the next is still
    forming - so the wait for first words is the model's first sentence, not the whole turn plus a
    stack of stock phrases. The stock phrases are gone: no acknowledgement line, no "I'll get back
    to you on that", no "ready for it?" gate, no cut at 260 characters. What remains unprompted is
    agent news at a lull, and a barge-in (Enter, or a spoken stop) that silences everything at once.
    """

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
        empty_turn_reply=DEFAULT_EMPTY_TURN_REPLY,
        interrupt_poll=DEFAULT_INTERRUPT_POLL,
        cancel_wait=DEFAULT_CANCEL_WAIT,
        read_pause=DEFAULT_READ_PAUSE,
        dormant_after=DEFAULT_DORMANT_AFTER,
        console=None,
        sleep=time.sleep,
        clock=time.monotonic,
        timings=False,
        outbox=None,
        interrupt=None,
        briefing=None,
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
        self.empty_turn_reply = empty_turn_reply
        self._unwritten = []  # lines spoken in its name that it didn't compose; told to it next turn
        self._waiting = []  # news drained from the outbox and not delivered yet
        self._announced = 0  # how many were waiting when the roll call was last read out
        self._clock = clock
        self._dormant_after = dormant_after
        self._last_engaged = clock()  # startup counts: they just launched it, so they are here
        self._update_offered = False  # a dormant-lull offer stands; the news waits to be taken
        self._briefing = briefing  # callable: the live fleet state, put before the brain each turn
        self._brain_streams = _accepts_streaming(brain)
        self._interrupt_poll = interrupt_poll
        self._cancel_wait = cancel_wait
        self._read_pause = read_pause  # a beat after a reply so they can read it before listening resumes
        self._console = console or Console()
        self._sleep = sleep
        self._timings = timings  # --timings: show how long each turn spent thinking vs. speaking
        self._outbox = outbox
        self._interrupt = interrupt  # set (e.g. by a keypress) to cut off whatever it's saying
        self._paused = False
        self._floor_watched = False  # true while a stop-watcher already holds the mic (see _say)

    def _interrupted(self):
        return self._interrupt is not None and self._interrupt.is_set()

    def _say(self, text, *, record=True, known=False):
        """Speak, unless they've cut in. Once the interrupt is set, every later line this turn stays
        unsaid, and a line already in progress is killed by the TTS. While it speaks, a background
        watcher listens for them saying "stop", which trips the same interrupt - so they can cut it off
        by voice, not just the Enter key. When a watcher already holds the mic, we don't open a second
        one - two readers on one mic corrupt each other. A voice hiccup is logged, not fatal - a
        failed utterance must never crash the loop (it did, and they lost the whole run)."""
        if self._interrupted():
            self._console.spoke("(left unsaid - they had cut in)")
            return
        if not known:
            # They are about to hear this as the Entity speaking, and the brain did not write it -
            # so the brain has to be told, or the two of them remember different conversations.
            self._unwritten.append(text)
        if record:  # a line already printed records itself; this is for the ones only they hear
            self._console.spoke(text)
        stop_watching = None if self._floor_watched else self._watch_for_spoken_stop()
        try:
            # Said, not written: an address becomes "the link" and a path becomes its filename. The
            # line above already showed the real thing, which is what they read and clicks - this is
            # only the difference between what is on the screen and what a person would say aloud.
            self._tts.speak(as_spoken(text), interrupt=self._interrupt)
        except Exception as exc:  # a failed utterance must never crash the loop - but it IS evidence
            self._console.spoke(f"(voice failed: {exc!r})")
        else:
            if self._interrupted():  # the utterance was killed partway - the record must say so,
                self._console.spoke("(cut off mid-utterance)")  # or a silenced line looks delivered
        finally:
            if stop_watching is not None:
                stop_watching()

    def _speak_reply(self, text, *, known=False):
        """Show the reply, then say it - the same words on screen as in their ear."""
        self._console.reply(text)
        self._say(text, record=False, known=known)

    def _pause_to_read(self):
        """A short beat after a reply before the mic reopens, so they aren't rushed off it - skipped if
        they've barged in (they're cutting in, not reading)."""
        if self._read_pause > 0 and not self._interrupted():
            self._sleep(self._read_pause)

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
                if catch_stop(speaking.is_set):  # they said "stop" while it was talking
                    self._interrupt.set()
            except Exception as exc:
                print(f"[voice-stop error] {exc!r}", file=sys.stderr)

        thread = threading.Thread(target=watch, daemon=True)
        thread.start()

        def stop():
            speaking.clear()  # the reply's done (or was cut) - let the watcher release the mic
            thread.join(timeout=1.5)

        return stop

    def _hold_the_floor(self):
        """One stop-watcher held for a whole turn - think plus every sentence of streamed audio -
        so a spoken "stop" lands whenever it comes, without two readers ever sharing the mic.
        Returns a release callable; safe to call once whichever way the turn ends."""
        stop_watching = self._watch_for_spoken_stop()
        self._floor_watched = stop_watching is not None

        def release():
            self._floor_watched = False
            if stop_watching is not None:
                stop_watching()

        return release

    def _deliver_outbox(self):
        """Say what the Entity has queued to say on its own - word from an agent.

        ONE agent's news is spoken as it lands. SEVERAL arriving together are read out numbered and
        then held, because run into one utterance they arrived as a wall. When several are ready,
        say which, and let the order be chosen; whichever is named is spoken then (see `_take_pick`).

        Whatever goes out goes out as ONE utterance, so a single stop silences all of it; they had
        to hit stop over and over while a report came at them line by line. It waits while they are
        recording, because it once broke in mid-sentence while they were talking.
        """
        if self._outbox is None:
            return
        # ALWAYS drain, even when it can't be said yet. The queue's "something is waiting" flag is
        # what makes the window's mic yield an empty turn, and it is only cleared by draining - so
        # returning early with it still set spun the loop forever and swallowed every submission they
        # made. Held news waits here instead, in hand, and goes out at the next opportunity.
        self._waiting.extend(self._outbox.drain())
        self._waiting = _newest_per_agent(self._waiting)
        if not self._waiting:
            self._announced = 0  # nothing outstanding, so the next single item is simply spoken
            self._update_offered = False
            return
        if self._they_are_talking():
            return
        if self._dormant():
            # They are off doing something else; news breaking in "out of nowhere" is a jolt.
            # One offer names who it is about, and the content waits for them to engage.
            if not self._update_offered:
                self._update_offered = True
                self._say(UPDATE_OFFER.format(what=self._whose_news()))
            return
        self._update_offered = False
        if self._announced:
            # A list has been read out and not worked through. Say it again only if it has changed,
            # or every trip round the loop would recite the same names at them.
            if len(self._waiting) != self._announced:
                self._announce()
            return
        if len(self._waiting) > 1:
            self._announce()
            return
        news = self._waiting.pop()
        self._console.heads_up(news)  # shown in full, and spoken the same
        # Brain-composed news is the brain's own sentence: spoken as known, so the unwritten-lines
        # ledger never reads its own words back to it as someone else's.
        self._say(news, record=False, known=getattr(news, "composed", False))

    def _announce(self):
        """Read out who is waiting, numbered, so one of them can be named."""
        self._announced = len(self._waiting)
        line = roll_call(self._waiting)
        self._console.heads_up(line)
        self._say(line, record=False)

    def _take_pick(self, heard):
        """They answered the roll call by naming one: say that one, and what is still waiting.

        A Turn if they were naming one, None if they were not - in which case this was an ordinary
        thing to say and the list simply stands. Only a terse answer counts as naming one (see
        `waiting.chosen`), so a sentence that happens to carry an agent's name is still their turn:
        answering it with a notice instead would lose the question.
        """
        place = chosen(heard, self._waiting)
        if place is None:
            return None
        news = self._waiting.pop(place)
        said = news if not self._waiting else f"{news}\n\n{roll_call(self._waiting)}"
        self._announced = len(self._waiting)
        self._console.heads_up(said)
        # Known only when the whole utterance is the brain's own sentence; with a roll call
        # appended, part of what they hear is app-authored and the ledger must carry it.
        self._say(said, record=False,
                  known=getattr(news, "composed", False) and said == news)
        return Turn(heard=heard, said=said)

    def _dormant(self):
        return (self._dormant_after is not None
                and self._clock() - self._last_engaged > self._dormant_after)

    def _whose_news(self):
        names = []
        for item in self._waiting:
            about = getattr(item, "about", None) or "your agents"
            if about not in names:
                names.append(about)
        return " and ".join(names)

    def _release_updates(self, heard):
        """They said the word: the held update goes out now, as this turn."""
        self._update_offered = False
        if len(self._waiting) > 1:
            self._announce()
            return Turn(heard=heard, said=roll_call(self._waiting))
        news = self._waiting.pop()
        self._console.heads_up(news)
        self._say(news, record=False, known=getattr(news, "composed", False))
        return Turn(heard=heard, said=str(news))

    def _they_are_talking(self):
        """Are they part-way through saying something? While they are, the Entity says nothing of its
        own accord - it once broke in while they were mid-sentence.

        The question used to be "is their mic on", which was the same question when the mic was a
        walkie-talkie: it was only live while they held a turn. The window's mic is a STATE and stays
        armed for the whole conversation, so that reading answered yes forever and nothing unprompted
        could ever be said at all. A mic that can't report (the terminal's) never blocks: it only
        yields between turns anyway.
        """
        talking = getattr(self._stt, "is_mid_utterance", None)
        return bool(talking and talking())

    def _think(self, heard, on_text=None):
        """Ask the brain off the main thread so the interrupt stays answerable the whole time. If
        they barge in while it's thinking, the call is cancelled and `_ThinkInterrupted` is raised
        so the loop drops the turn. Re-raises whatever the brain raised, so the caller's error
        handling is unchanged. `on_text` streams the reply's text out as it is written."""
        outcome = {}
        done = threading.Event()

        def work():
            try:
                if on_text is not None:
                    outcome["reply"] = self._brain.respond(heard, on_text=on_text)
                else:
                    outcome["reply"] = self._brain.respond(heard)
            except BaseException as exc:  # carry it back to the main thread to re-raise in context
                outcome["error"] = exc
            finally:
                done.set()

        threading.Thread(target=work, daemon=True).start()
        while not done.wait(self._interrupt_poll):
            if self._interrupted():  # they cut in - cancel the call and abandon the turn
                self._cancel_think(done)
                raise _ThinkInterrupted
        if "error" in outcome:
            raise outcome["error"]
        return outcome["reply"]

    def _cancel_think(self, done):
        """Tell the brain to drop the in-flight call, then wait for the worker to unwind before
        returning - so the next turn never starts a second brain call overlapping this one. A brain
        with no `interrupt` (e.g. a fake) can't be cancelled; we still wait out the bounded window."""
        interrupt = getattr(self._brain, "interrupt", None)
        if interrupt is not None:
            try:
                interrupt()
            except Exception as exc:
                print(f"[interrupt error] {exc!r}", file=sys.stderr)
        done.wait(self._cancel_wait)

    def turn(self):
        if self._interrupt is not None:
            self._interrupt.clear()  # a fresh turn; forget any leftover "stop" from the last one
        self._deliver_outbox()  # say any queued agent news before we start listening again
        if not self._paused:  # asleep it's only watching for the wake word - don't claim otherwise
            self._console.listening()
        heard = self._stt.listen()
        if not heard.strip():
            # An empty turn that still ended on "over" means they said only the terminator - let them
            # know it registered (the "✓ got it" cue already printed) instead of leaving dead air.
            if getattr(self._stt, "caught_terminator", False):
                self._say(self.empty_turn_reply)
            return None
        canonical = _canonical(heard)
        farewell = _ends_with_command(canonical, self._farewells)
        if self._paused and not farewell and not _wakes(canonical, self._resumes):
            # Asleep, and it's neither a wake word nor a goodbye - so it's the TV, or someone else in
            # the room. Don't transcribe it back at them; just show that it landed and was dropped.
            self._console.ignored()
            return None
        self._console.heard(heard)  # show what was transcribed before we act on it
        self._last_engaged = self._clock()  # they spoke: present again, whatever the clock said
        if farewell:
            self._speak_reply(self.farewell_reply)
            return Turn(heard=heard, said=self.farewell_reply, farewell=True)
        if self._paused:  # a wake word - the only other thing that gets through
            self._paused = False
            self._speak_reply(self.resume_reply)
            return Turn(heard=heard, said=self.resume_reply)
        if _ends_with_command(canonical, self._suspends):  # "stop listening" puts it to sleep, doesn't quit
            self._paused = True
            self._speak_reply(self.suspend_reply)
            return Turn(heard=heard, said=self.suspend_reply)
        if self._update_offered and self._waiting and _canonical(heard) in _GO_AHEADS:
            return self._release_updates(heard)  # they said the word; the held update is the turn
        if self._waiting:  # they may be naming one of the agents the roll call just read out
            picked = self._take_pick(heard)
            if picked is not None:
                return picked
        return self._answer(heard)

    def _with_system_notes(self, heard):
        """Their words, with what the brain would otherwise have no way of knowing put in front:
        the live fleet briefing, and everything said in its name since that it did not write."""
        notes = ""
        if self._briefing is not None:
            facts = str(self._briefing()).strip()
            if facts:
                notes += BRIEFING_NOTICE.format(briefing=facts)
        unwritten, self._unwritten = self._unwritten, []
        if unwritten:
            notes += UNWRITTEN_NOTICE.format(lines="\n".join(f"- {line}" for line in unwritten))
        return notes + heard

    def _answer(self, heard):
        """Think, and speak the reply as it is written.

        With a streaming voice and a streaming brain, each sentence sounds while the next is still
        forming, and the loop waits out the audio before listening again. With either half unable
        to stream (the system voice, a plain fake), the reply is spoken whole once it lands - the
        same behavior this loop always had, minus the stock phrases around it."""
        self._console.thinking()  # a "(thinking…)" indicator so a pause doesn't read as a hang
        release_floor = self._hold_the_floor()
        open_stream = getattr(self._tts, "stream", None)
        reply = None
        if open_stream is not None and self._brain_streams:
            # Sentences are synthesized in their spoken form (a path becomes its filename) while
            # the record keeps the real text - the screen shows what gets clicked.
            reply = open_stream(interrupt=self._interrupt, spoken_form=as_spoken)
        think_start = time.monotonic()
        try:
            said = self._think(self._with_system_notes(heard),
                               on_text=reply.add if reply is not None else None)
        except _ThinkInterrupted:  # they cut the thinking off - no reply, straight back to listening
            self._settle(reply)
            release_floor()
            return None
        except Exception as exc:  # tell them the real cause - it reaches them nowhere else
            self._settle(reply)
            said = self.error_reply.format(cause=_cause(exc))
            self._speak_reply(said)
            release_floor()
            return Turn(heard=heard, said=said, error=True)
        think_time = time.monotonic() - think_start
        if not said.strip():
            # Nothing to say - the turn completed silently. A blank "entity>" line or an empty
            # utterance would be noise.
            self._settle(reply)
            release_floor()
            return Turn(heard=heard, said="")
        speak_start = time.monotonic()
        if reply is not None:
            # On screen the moment the text is complete - the audio is still going out, and
            # reading along beats being read to and shown the words afterwards.
            self._console.reply(said)
            reply.done()  # then wait out the rest of the audio
            if self._interrupted():  # the audio was cut partway - the record must say so
                self._console.spoke("(cut off mid-utterance)")
        else:
            self._speak_reply(said, known=True)  # if they hit Enter while it talks, this is cut off
        release_floor()
        if self._timings:
            self._console.timing(think=think_time, speak=time.monotonic() - speak_start)
        self._pause_to_read()
        return Turn(heard=heard, said=said)

    def _settle(self, reply):
        """Let an open reply stream wind down (whatever was already spoken has been heard; the rest
        drains unspoken once the interrupt is set, or was never fed)."""
        if reply is not None:
            reply.done()

    def run(self, should_continue=lambda: True, on_turn=None):
        while should_continue():
            result = self.turn()
            if result is None:
                continue
            if on_turn is not None:
                on_turn(result)
            if result.farewell:
                break
