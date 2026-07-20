import re
import sys
import threading
import time
from dataclasses import dataclass

from entity.console import Console
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

# A long or slow answer isn't dumped on them - it's offered first, and only spoken once they say yes,
# so a wall of text (or a reply they've stopped caring about) never just barges out of the speaker.
DEFAULT_READY_QUESTION = "I've got a longer answer for you - ready for it?"
# Replies over this many characters are gated behind DEFAULT_READY_QUESTION. Set to None to never
# gate. Kept a few sentences long, since the persona already pushes hard for brevity - only a
# genuinely big reply should have to wait for a yes.
DEFAULT_LONG_ANSWER_CHARS = 320

# How long a reply may BE. Not how much of it is read aloud - they disliked hearing only part of
# what was written, and said the real answer is that it shouldn't send such long messages. So a
# reply is cut to this at the seam of a sentence, and what they read is what they hear.
DEFAULT_SPOKEN_CHARS = 260

# Said back to the brain on the turn AFTER one of its replies was cut. Truncating silently taught
# it nothing - it never saw the cut, so each turn began with no evidence any of it had happened,
# and it went on writing long. This is the only way the limit can be learned rather than merely
# suffered.
TRUNCATION_NOTICE = (
    "[System note, not from the user: your last reply ran to {wrote} characters and was CUT OFF at "
    "{limit} - they never saw or heard the rest of it. Answer in one or two short sentences.]\n\n"
)

# Wraps a detached answer when it finally goes out. By then they have said other things and moved
# on, so an answer that arrives bare reads as a non-sequitur: "you responded to something that I
# said several messages ago... it doesn't make sense to phrase things like we were just talking
# about logs." Their own words are the one preface that makes a late answer land as an answer.
LATE_ANSWER_PREFACE = 'On "{question}" - {answer}'

# How much of their question is quoted back. Enough to recognise; a whole spoken paragraph would
# bury the answer it prefaces.
LATE_QUESTION_CHARS = 90

# The answer given when a SLOW directive succeeded with nothing to say. A prompt silent success
# really is silent - the ack covered it - but a slow one already said "I'll get back to you on
# that.", and a promise to get back that is never followed by anything is this program's original
# sin. The smallest closure that names what it closes.
LATE_CLOSURE = "handled."

# Said back to the brain on the turn AFTER anything was spoken in its name that it did not write -
# the acknowledgement, the handoff line, an agent's notice, a canned confirmation.
#
# The user hears ONE Entity. The brain only ever knew the half of it that it composed, so he could
# quote a line at it and be told, truthfully, "I have no record of typing that myself" - which from
# where he sits is a thing that said something and then denied saying it. His words: "a basic
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

# Whether they said yes to "ready for it?" (see _is_affirmative for the full rules).
_AFFIRMATIVES = (
    "yes", "yeah", "yep", "yup", "sure", "okay", "ok", "ready", "please", "now",
    "go ahead", "go for it", "do it", "hit me", "hear it", "let's hear", "sounds good", "please do",
)
_NEGATIVES = ("no", "nope", "nah", "not", "dont", "later", "wait", "hold", "stop", "skip")

# Spoken the instant a turn is heard, before the brain even starts - so the user never talks into
# dead air waiting to find out they were heard. One plain line they asked for by name (the varied
# ones came out as awkward TTS - "Mm-hm." read aloud as "m m"). This is their word too: they
# counted the stock phrases they were getting per turn, called the variety and the length of them a
# waste of their time, and said a single minimal "Got it." was all that should have been necessary.
DEFAULT_ACK = "Got it."

# Waiting is answered ONCE, by handing the question to the background (see DEFAULT_DETACH_AFTER) -
# not by a stream of "still working" updates, which they found annoying rather than reassuring. These
# stay for a caller that wants the old cadence; both sit past the detach, so neither fires by
# default: a wait ends by getting an answer or by being told they'll be got back to.
DEFAULT_PATIENCE = 60.0
DEFAULT_CHECK_IN = 60.0

# While the brain thinks, re-check this often for a barge-in, so cutting a slow think off feels
# instant rather than waiting out the next check-in.
DEFAULT_INTERRUPT_POLL = 0.05
# After telling the brain to cancel, wait up to this long for the call to actually unwind before
# moving on - so the loop never starts a second brain call overlapping a half-cancelled one.
DEFAULT_CANCEL_WAIT = 10.0

# Once a think has run this long it stops blocking the conversation: the Entity says it'll keep at
# it, the call runs on in the background, and the finished answer is offered later. None disables
# detaching (a slow think just blocks with check-ins, as before). Well past the check-in cadence so
# only a genuinely long call detaches.
DEFAULT_DETACH_AFTER = 5.0
# Their words, verbatim, every time. Varying it was a fix for hearing one canned sentence four
# times; they then heard the variations and asked for exactly this line instead - flowery
# alternatives are worse than repetition when all they want to know is that it heard them.
DEFAULT_DETACH_REPLIES = ("I'll get back to you on that.",)

# After a reply, wait this long before listening again, so they get a beat to read it rather than the
# mic reopening the instant the voice stops. 0 disables (default; the app turns it on for voice runs).
DEFAULT_READ_PAUSE = 0.0


class _ThinkInterrupted(Exception):
    """Internal signal that a barge-in cancelled the brain call - the turn is abandoned and the
    loop goes straight back to listening, with no reply and no error spoken."""


class _ThinkDetached(Exception):
    """Internal signal that a slow think was handed to the background - the turn ends and the loop
    listens again; the finished answer is offered on a later turn."""


def _humanize_elapsed(seconds):
    seconds = max(5, int(round(seconds / 5.0)) * 5)  # nearest 5s, never below 5
    if seconds < 60:
        return f"about {seconds} seconds"
    minutes, rest = divmod(seconds, 60)
    unit = "minute" if minutes == 1 else "minutes"
    if rest == 0:
        return f"about {minutes} {unit}"
    return f"about {minutes} {unit} and {rest} seconds"


# A number that opens a step ("1. Open the console", "2) Enable the API"). Two or more of them and
# this is a list, not a passing mention of "step 1." in a sentence.
_ENUMERATOR = re.compile(r"(?:^|\s)\d+[.)]\s+\S")


def _is_walkthrough(text):
    """Are these the numbered steps they asked for, rather than chatter?

    Cutting one is worse than saying nothing: `_opening` splits on sentence ends, "1." looks exactly
    like one, and so the cut fell between the number and its step - they were handed a list marker
    with no step attached and had to say "all you said was the number one and nothing more". The
    persona already promises them a walkthrough complete, however many lines it takes; brevity
    governs what the Entity volunteers, never something they explicitly asked to be told.
    """
    return len(_ENUMERATOR.findall(str(text))) >= 2


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


def _opening(text, limit):
    """The first sentence or two of `text`, whole sentences only, or all of it if it's already
    short. Never mid-word: a voice cut off mid-sentence sounds like a fault."""
    said = " ".join(str(text).split())
    if limit is None or len(said) <= limit:
        return said
    kept = ""
    for sentence in re.split(r"(?<=[.!?])\s", said):
        if kept and len(kept) + len(sentence) + 1 > limit:
            break
        kept = f"{kept} {sentence}".strip()
    if not kept:  # one enormous sentence: stop at a word boundary rather than mid-word
        kept = said[:limit].rsplit(" ", 1)[0] + "…"
    return kept


def _default_reassurance(seconds):
    # "processing your request", not "working on it" - the Entity triages and relays, it isn't doing
    # the agent's actual work, and they found "working on it" misleading.
    return f"Still processing your request - {_humanize_elapsed(seconds)} so far."


# Words that flip the yes word right after them ("not now", "don't go ahead"). "t" is here because
# _canonical splits contractions apart - "don't"/"won't"/"can't" all end in a bare "t" right before
# the yes they negate.
_NEGATORS = ("not", "no", "dont", "t", "never", "nah", "nope")


def _is_affirmative(heard):
    """Is there a yes in it? A yes ANYWHERE counts, even alongside negative words: with a TV in the
    room their real "yes, I am ready" arrives wrapped in unrelated chatter, and a stray "not" from the
    TV once vetoed their yes and cost them the answer they were promised. A false yes just speaks
    something they half-wanted; a false no throws it away. The one exception: a yes directly preceded
    by a negator ("not now") is that negation, not a yes."""
    words = _canonical(heard).split()
    for index, word in enumerate(words):
        matched = (word,) in _AFFIRMATIVE_RUNS or any(
            run == tuple(words[index:index + len(run)]) for run in _AFFIRMATIVE_RUNS if len(run) > 1
        )
        if matched and (index == 0 or words[index - 1] not in _NEGATORS):
            return True
    return False


_AFFIRMATIVE_RUNS = tuple(tuple(yes.split()) for yes in _AFFIRMATIVES)


def _is_negative(heard):
    """Is there a no in it? Only consulted once _is_affirmative found no yes."""
    words = _canonical(heard).split()
    return any(neg in words for neg in _NEGATIVES)


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
        suspends=DEFAULT_SUSPENDS,
        resumes=DEFAULT_RESUMES,
        farewell_reply=DEFAULT_FAREWELL_REPLY,
        error_reply=DEFAULT_ERROR_REPLY,
        suspend_reply=DEFAULT_SUSPEND_REPLY,
        resume_reply=DEFAULT_RESUME_REPLY,
        empty_turn_reply=DEFAULT_EMPTY_TURN_REPLY,
        ready_question=DEFAULT_READY_QUESTION,
        detach_replies=DEFAULT_DETACH_REPLIES,
        acknowledgement=DEFAULT_ACK,
        reassurer=None,
        patience=DEFAULT_PATIENCE,
        check_in=DEFAULT_CHECK_IN,
        interrupt_poll=DEFAULT_INTERRUPT_POLL,
        cancel_wait=DEFAULT_CANCEL_WAIT,
        detach_after=DEFAULT_DETACH_AFTER,
        long_answer_chars=DEFAULT_LONG_ANSWER_CHARS,
        spoken_chars=DEFAULT_SPOKEN_CHARS,
        read_pause=DEFAULT_READ_PAUSE,
        console=None,
        sleep=time.sleep,
        timings=False,
        outbox=None,
        interrupt=None,
        wake=None,
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
        self.ready_question = ready_question
        self.detach_replies = detach_replies
        self._detached_count = 0  # how many calls have gone to the background, to vary the wording
        self._long_answer_chars = long_answer_chars
        self._spoken_chars = spoken_chars
        self._cut_last_reply = None  # (written, limit) when the last reply was cut, else None
        self._unwritten = []  # lines spoken in its name that it didn't compose; told to it next turn
        self._detach_after = detach_after
        self._offered = None  # a long/slow answer spoken only once they say yes to "ready for it?"
        self._waiting = []  # news drained from the outbox and not delivered yet
        self._announced = 0  # how many were waiting when the roll call was last read out
        self._background = None  # a slow think handed off, still running: {"done", "outcome"}
        self._wake = wake  # event the mic waits on; set to break a lull when news is ready to speak
        self._acknowledgement = acknowledgement
        self._reassure = reassurer or _default_reassurance
        self._patience = patience
        self._check_in = check_in
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
        by voice, not just the Enter key. When a watcher already holds the mic (a check-in spoken
        mid-think), we don't open a second one - two readers on one mic corrupt each other. A voice
        hiccup is logged, not fatal - a failed utterance must never crash the loop (it did, and they
        lost the whole run)."""
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
            self._tts.speak(text, interrupt=self._interrupt)
        except Exception as exc:  # a failed utterance must never crash the loop - but it IS evidence
            self._console.spoke(f"(voice failed: {exc!r})")
        else:
            if self._interrupted():  # the utterance was killed partway - the record must say so,
                self._console.spoke("(cut off mid-utterance)")  # or a silenced line looks delivered
        finally:
            if stop_watching is not None:
                stop_watching()

    def _speak_reply(self, text, *, known=False, whole_thing=False):
        """Cut it to a length worth hearing, then show and say exactly that.

        They disliked reading a wall they'd only heard the start of, so what is on screen and what
        is in their ear are the same words - the cut happens once, up front, to both. Steps they
        asked for are exempt and go out whole (see `_is_walkthrough`): brevity is for chatter.
        `whole_thing` is the other exemption - a failure, where cutting the one line that explains a
        wedged session is the same defect as never printing it.

        `known` says the brain is already aware of this line - it composed it, or the persona tells
        it standingly that it goes out. Everything else joins the list it is told next turn.
        """
        whole = text
        limit = None if whole_thing or _is_walkthrough(whole) else self._spoken_chars
        text = _opening(text, limit)
        # Remember a cut so the next turn can say so: a limit nothing ever reports is not a limit
        # anything can learn from.
        self._cut_last_reply = (len(whole), self._spoken_chars) if len(text) < len(whole) else None
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

    def _deliver_outbox(self):
        """Say what the Entity has queued to say on its own - word from an agent.

        ONE agent's news is spoken as it lands. SEVERAL arriving together are read out numbered and
        then held, because run into one utterance they arrived as a wall - and a long enough one to
        be offered as "ready for it?" rather than simply heard. What was asked for is the other
        shape: when several are ready, say which, and let the order be chosen. Whichever is named
        is spoken then (see `_take_pick`).

        Whatever goes out goes out as ONE utterance, so a single stop silences all of it; they had
        to hit stop over and over while a report came at them line by line. It waits while they are
        recording, because it once broke in mid-sentence while they were talking. And a single
        piece of news that is long is OFFERED, not read out: a wall of an agent's own words is the
        thing they most want to be insulated from.
        """
        if self._outbox is None:
            return
        # ALWAYS drain, even when it can't be said yet. The queue's "something is waiting" flag is
        # what makes the window's mic yield an empty turn, and it is only cleared by draining - so
        # returning early with it still set spun the loop forever and swallowed every submission they
        # made. Held news waits here instead, in hand, and goes out at the next opportunity.
        self._waiting.extend(self._outbox.drain())
        if not self._waiting:
            self._announced = 0  # nothing outstanding, so the next single item is simply spoken
            return
        if self._offered is not None or self._they_are_talking():
            return
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
        self._console.heads_up(news)  # shown in full, however it gets spoken
        if self._long_answer_chars is not None and len(news) > self._long_answer_chars:
            self._offered = news
            self._say(self.ready_question, record=False)
            return
        self._say(news, record=False)

    def _announce(self):
        """Read out who is waiting, numbered, so one of them can be named."""
        self._announced = len(self._waiting)
        line = roll_call(self._waiting)
        self._console.heads_up(line)
        self._say(line, record=False)

    def _take_pick(self, heard):
        """They answered the roll call by naming one: say that one, and what is still waiting.

        A Turn if they were naming one, None if they were not - in which case this was an ordinary
        thing to say and the list simply stands, the way a pending offer does. Only a terse answer
        counts as naming one (see `waiting.chosen`), so a sentence that happens to carry an agent's
        name is still their turn: answering it with a notice instead would lose the question.
        """
        place = chosen(heard, self._waiting)
        if place is None:
            return None
        news = self._waiting.pop(place)
        said = news if not self._waiting else f"{news}\n\n{roll_call(self._waiting)}"
        self._announced = len(self._waiting)
        self._console.heads_up(said)
        self._say(said, record=False)
        return Turn(heard=heard, said=said)

    def _they_are_talking(self):
        """Are they part-way through saying something? While they are, the Entity says nothing of its
        own accord - it once broke in while they were mid-sentence.

        The question used to be "is their mic on", which was the same question when the mic was a
        walkie-talkie: it was only live while they held a turn. The window's mic is a STATE and stays
        armed for the whole conversation, so that reading answered yes forever and nothing unprompted
        - agent news, a collected answer - could ever be said at all. A mic that can't report (the
        terminal's) never blocks: it only yields between turns anyway.
        """
        talking = getattr(self._stt, "is_mid_utterance", None)
        return bool(talking and talking())

    def _think(self, heard, question=None):
        """Ask the brain off the main thread so a slow reply can't read as a crash. The first
        check-in comes after `patience`, then it keeps checking in every `check_in` seconds - each
        time saying how long it's been - until the reply lands. If they barge in while it's thinking,
        the call is cancelled and `_ThinkInterrupted` is raised so the loop drops the turn. If it
        runs past `detach_after`, it's handed to the background and `_ThinkDetached` is raised so the
        loop is freed and the answer is offered later - remembering `question` (their words as they
        said them, without the system notes prefixed to `heard`) so the late answer can say what it
        answers. Re-raises whatever the brain raised, so the caller's error handling is unchanged."""
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
        # Listen for a spoken "stop" for the whole think, not just during a check-in - so they can cut
        # off a slow brain call by voice even in its silent stretches, the same as pressing Enter.
        stop_watching = self._watch_for_spoken_stop()
        self._floor_watched = stop_watching is not None
        try:
            next_check_in = start + self._patience
            detach_at = start + self._detach_after if self._detach_after is not None else None
            while not done.is_set():
                if self._interrupted():  # they cut in - cancel the call and abandon the turn
                    self._cancel_think(done)
                    raise _ThinkInterrupted
                if detach_at is not None and time.monotonic() >= detach_at:  # too slow - background it
                    self._speak_reply(self._detach_line())
                    self._detach(done, outcome, question if question is not None else heard)
                    raise _ThinkDetached
                deadline = next_check_in if detach_at is None else min(next_check_in, detach_at)
                timeout = min(self._interrupt_poll, max(0.0, deadline - time.monotonic()))
                if done.wait(timeout):
                    break
                now = time.monotonic()
                if now >= next_check_in:  # still thinking - tell them how long, then keep waiting
                    self._say(self._reassure(now - start))
                    next_check_in = now + self._check_in
        finally:
            self._floor_watched = False
            if stop_watching is not None:
                stop_watching()
        if "error" in outcome:
            raise outcome["error"]
        return outcome["reply"]

    def _detach_line(self):
        """The next way of saying "this is taking a while" - cycled, so a session where several
        calls run long doesn't repeat one canned sentence at them."""
        line = self.detach_replies[self._detached_count % len(self.detach_replies)]
        self._detached_count += 1
        return line

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

    def _detach(self, done, outcome, question):
        """Leave the slow call running on its worker and remember it - with the question it is
        answering, so the answer can say what it answers when it finally arrives. A reaper breaks
        the next lull the moment it lands, so the finished answer reaches them promptly rather than
        waiting for them to speak first."""
        self._background = {"done": done, "outcome": outcome, "question": question}
        if self._wake is not None:
            threading.Thread(target=self._reap, args=(done,), daemon=True).start()

    def _late_reply(self, background):
        """A detached answer, tied back to what it answers. By the time it lands they have moved on,
        and a bare answer to a question from seven messages ago reads as a non-sequitur - they had
        to count the messages back to work out what it belonged to. Their own words are the preface
        that makes it land as an answer."""
        reply = background["outcome"].get("reply")
        if reply is None:  # it failed; a background best-effort's failure stays dropped
            return None
        question = _opening(" ".join(background["question"].split()), LATE_QUESTION_CHARS)
        # An empty reply is a silent success - but "I'll get back to you on that." was already said,
        # so this call owes a closure, however small (see LATE_CLOSURE).
        return LATE_ANSWER_PREFACE.format(question=question, answer=reply.strip() or LATE_CLOSURE)

    def _reap(self, done):
        done.wait()
        self._wake.set()  # break the mic's lull so the loop cycles round and delivers the answer

    def _collect_background(self):
        """If a detached call has finished, deliver its answer (a failure is dropped - a background
        best-effort, not worth surfacing as a glitch). Runs at the top of a turn.

        Delivered on the same terms as any other reply: short enough, and it is simply said. Offering
        every collected answer regardless of size announced "I've got a longer answer for you" ahead
        of a sixteen-character sentence, and he asked why - then the offer sat unclaimed for
        twenty-one minutes, because an answer he has to say yes to is one more thing to notice.
        """
        background = self._background
        if background is None or not background["done"].is_set():
            return
        if self._they_are_talking():
            return  # mid-sentence; a finished answer waits, exactly as agent news does
        self._background = None
        reply = self._late_reply(background)
        if reply is None or self._offered is not None:
            return
        if self._should_gate(reply):
            self._offered = reply
            self._speak_reply(self.ready_question)
        else:
            self._speak_reply(reply, known=True)

    def turn(self):
        if self._interrupt is not None:
            self._interrupt.clear()  # a fresh turn; forget any leftover "stop" from the last one
        self._deliver_outbox()  # say any queued agent news before we start listening again
        self._collect_background()  # a slow answer that has since landed is offered here
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
        if self._offered is not None:  # they're answering "ready for it?" from a held long/slow reply
            return self._resolve_offer(heard)
        if self._waiting:  # they may be naming one of the agents the roll call just read out
            picked = self._take_pick(heard)
            if picked is not None:
                return picked
        if self._background is not None:
            self._settle_background()  # keep a promise that has come due; drop one that hasn't
        return self._answer(heard)

    def _settle_background(self):
        """They spoke while a detached call was outstanding. What happens next turns entirely on
        whether that call has ANSWERED yet.

        If it has, that answer is the thing they were promised - say it, before taking their new
        turn. It cost minutes to produce, it is sitting right here, and it is very often the exact
        thing they are asking about. Cancelling it instead was the worst bug this program has had:
        they asked, were told "I'll get back to you on that", and every time they pushed for the
        answer, the push is what destroyed it. Half an hour of that reads as being ignored, because
        from where they sit it IS - pressing harder made the answer strictly less likely.

        If it hasn't answered, cancel it: there's only one session, so it can't answer them until
        that call ends, and bouncing them with a canned "still finishing your last one" threw their
        words away and locked them out of the conversation entirely. Work they've given up on is
        outranked by what they're saying now - an answer already in hand never is.
        """
        background = self._background
        self._background = None
        if background["done"].is_set():
            reply = self._late_reply(background)  # a failure stays dropped, as it always has
            if reply is not None:
                self._speak_reply(reply, known=True)
            return
        self._console.dropped()  # so the promise it made doesn't just silently evaporate
        self._cancel_think(background["done"])  # unwind it before their turn starts a new call

    def _with_system_notes(self, heard):
        """Their words, prefixed with what the brain would otherwise have no way of knowing: that
        its last reply was cut, and everything said in its name since that it did not write."""
        notes = ""
        cut, self._cut_last_reply = self._cut_last_reply, None
        if cut is not None:
            wrote, limit = cut
            notes += TRUNCATION_NOTICE.format(wrote=wrote, limit=limit)
        unwritten, self._unwritten = self._unwritten, []
        if unwritten:
            notes += UNWRITTEN_NOTICE.format(lines="\n".join(f"- {line}" for line in unwritten))
        return notes + heard

    def _answer(self, heard):
        """Acknowledge, think, and speak the reply - unless it's long enough to gate, in which case
        it's held and offered first (see _offer)."""
        # `known`: this one fires on every single turn, so the persona states it once as a standing
        # fact rather than the ledger repeating it into every prompt for the rest of the session.
        self._say(self._acknowledgement, known=True)
        self._console.thinking()  # a "(thinking…)" indicator so a pause doesn't read as a hang
        think_start = time.monotonic()
        try:
            said = self._think(self._with_system_notes(heard), question=heard)
        except _ThinkInterrupted:  # they cut the thinking off - no reply, straight back to listening
            return None
        except _ThinkDetached:  # too slow - it's running in the background; offered when it lands
            return None
        except Exception as exc:  # tell them the real cause - it reaches them nowhere else
            said = self.error_reply.format(cause=_cause(exc))
            self._speak_reply(said, whole_thing=True)
            return Turn(heard=heard, said=said, error=True)
        think_time = time.monotonic() - think_start
        if not said.strip():
            # Nothing to say - a directive succeeded and the ack already confirmed receipt. The
            # turn completed; a blank "entity>" line or an empty utterance would be noise.
            return Turn(heard=heard, said="")
        if self._should_gate(said):
            return self._offer(heard, said)
        speak_start = time.monotonic()
        self._speak_reply(said, known=True)  # if they hit Enter while it was talking, this is cut off
        if self._timings:
            self._console.timing(think=think_time, speak=time.monotonic() - speak_start)
        self._pause_to_read()
        return Turn(heard=heard, said=said)

    def _should_gate(self, reply):
        return self._long_answer_chars is not None and len(reply) > self._long_answer_chars

    def _offer(self, heard, answer):
        """Hold a long answer and ask if they want it, rather than dumping it. Their next turn's yes
        releases it (see _resolve_offer)."""
        self._offered = answer
        self._speak_reply(self.ready_question)
        return Turn(heard=heard, said=self.ready_question)

    def _resolve_offer(self, heard):
        """Their reply to "ready for it?": a yes speaks the held answer, a no drops it, and speech
        that answers NEITHER way - TV chatter, or them moving on to something else - is handled as an
        ordinary turn with the offer left standing, so noise can't destroy an answer they never got
        to accept or refuse."""
        if _is_affirmative(heard):
            answer, self._offered = self._offered, None
            self._speak_reply(answer, known=True)
            self._pause_to_read()
            return Turn(heard=heard, said=answer)
        if _is_negative(heard):
            self._offered = None
        return self._answer(heard)

    def run(self, should_continue=lambda: True, on_turn=None):
        while should_continue():
            result = self.turn()
            if result is None:
                continue
            if on_turn is not None:
                on_turn(result)
            if result.farewell:
                break
