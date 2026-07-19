import sys
import threading
import time
from dataclasses import dataclass

from entity.console import Console
from entity.phrases import canonical as _canonical
from entity.phrases import ends_with_command as _ends_with_command
from entity.phrases import wakes as _wakes

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
DEFAULT_ERROR_REPLY = "Sorry, my mind glitched for a second - say that again?"
# He ended a turn ("over") but said nothing in it. Rather than ignore him - which just makes him
# repeat "over" wondering if he was heard - acknowledge that the turn registered and invite him on.
DEFAULT_EMPTY_TURN_REPLY = "Go ahead."
DEFAULT_SUSPEND_REPLY = "Resting. Say 'hey Entity' when you want me back."
DEFAULT_RESUME_REPLY = "Back with you."

# A long or slow answer isn't dumped on him - it's offered first, and only spoken once he says yes,
# so a wall of text (or a reply he's stopped caring about) never just barges out of the speaker.
DEFAULT_READY_QUESTION = "I've got a longer answer for you - ready for it?"
# Replies over this many characters are gated behind DEFAULT_READY_QUESTION. Set to None to never
# gate. Kept a few sentences long, since the persona already pushes hard for brevity - only a
# genuinely big reply should have to wait for a yes.
DEFAULT_LONG_ANSWER_CHARS = 320

# Whether he said yes to "ready for it?" (see _is_affirmative for the full rules).
_AFFIRMATIVES = (
    "yes", "yeah", "yep", "yup", "sure", "okay", "ok", "ready", "please", "now",
    "go ahead", "go for it", "do it", "hit me", "hear it", "let's hear", "sounds good", "please do",
)
_NEGATIVES = ("no", "nope", "nah", "not", "dont", "later", "wait", "hold", "stop", "skip")

# Spoken the instant a turn is heard, before the brain even starts - so the user never talks into
# dead air waiting to find out he was heard. One plain line he asked for by name (the varied ones
# came out as awkward TTS - "Mm-hm." read aloud as "m m").
DEFAULT_ACK = "Message received."

# Waiting is answered ONCE, by handing the question to the background (see DEFAULT_DETACH_AFTER) -
# not by a stream of "still working" updates, which he found annoying rather than reassuring. These
# stay for a caller that wants the old cadence; both sit past the detach, so neither fires by
# default: a wait ends by getting an answer or by being told he'll be got back to.
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
# Said in turn, never the same one twice running: he heard one canned sentence four times in a
# single session and told us it was unnatural and disconcerting. Short, because this is the whole
# of what he wants to hear while he waits - "I'll get back to you on that".
DEFAULT_DETACH_REPLIES = (
    "I'll get back to you on that.",
    "Let me look into that - I'll come back to you.",
    "That one needs a minute. I'll bring it to you.",
    "On it - I'll get back to you.",
)

# After a reply, wait this long before listening again, so he gets a beat to read it rather than the
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


def _default_reassurance(seconds):
    # "processing your request", not "working on it" - the Entity triages and relays, it isn't doing
    # the agent's actual work, and he found "working on it" misleading.
    return f"Still processing your request - {_humanize_elapsed(seconds)} so far."


# Words that flip the yes word right after them ("not now", "don't go ahead"). "t" is here because
# _canonical splits contractions apart - "don't"/"won't"/"can't" all end in a bare "t" right before
# the yes they negate.
_NEGATORS = ("not", "no", "dont", "t", "never", "nah", "nope")


def _is_affirmative(heard):
    """Is there a yes in it? A yes ANYWHERE counts, even alongside negative words: with a TV in the
    room his real "yes, I am ready" arrives wrapped in unrelated chatter, and a stray "not" from the
    TV once vetoed his yes and cost him the answer he was promised. A false yes just speaks
    something he half-wanted; a false no throws it away. The one exception: a yes directly preceded
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
        self._detach_after = detach_after
        self._offered = None  # a long/slow answer spoken only once he says yes to "ready for it?"
        self._background = None  # a slow think handed off, still running: {"done", "outcome"}
        self._wake = wake  # event the mic waits on; set to break a lull when news is ready to speak
        self._acknowledgement = acknowledgement
        self._reassure = reassurer or _default_reassurance
        self._patience = patience
        self._check_in = check_in
        self._interrupt_poll = interrupt_poll
        self._cancel_wait = cancel_wait
        self._read_pause = read_pause  # a beat after a reply so he can read it before listening resumes
        self._console = console or Console()
        self._sleep = sleep
        self._timings = timings  # --timings: show how long each turn spent thinking vs. speaking
        self._outbox = outbox
        self._interrupt = interrupt  # set (e.g. by a keypress) to cut off whatever it's saying
        self._paused = False
        self._floor_watched = False  # true while a stop-watcher already holds the mic (see _say)

    def _interrupted(self):
        return self._interrupt is not None and self._interrupt.is_set()

    def _say(self, text, *, record=True):
        """Speak, unless he's cut in. Once the interrupt is set, every later line this turn stays
        unsaid, and a line already in progress is killed by the TTS. While it speaks, a background
        watcher listens for him saying "stop", which trips the same interrupt - so he can cut it off
        by voice, not just the Enter key. When a watcher already holds the mic (a check-in spoken
        mid-think), we don't open a second one - two readers on one mic corrupt each other. A voice
        hiccup is logged, not fatal - a failed utterance must never crash the loop (it did, and he
        lost the whole run)."""
        if self._interrupted():
            self._console.spoke("(left unsaid - he had cut in)")
            return
        if record:  # a line already printed records itself; this is for the ones only he hears
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

    def _speak_reply(self, text):
        """Print the line to the terminal, then speak it - so he can read the reply as it's said,
        not only hear it go by. Used for whatever a turn returns as its `said`; the ack and check-ins
        stay off the terminal, though the record keeps them."""
        self._console.reply(text)
        self._say(text, record=False)

    def _pause_to_read(self):
        """A short beat after a reply before the mic reopens, so he isn't rushed off it - skipped if
        he's barged in (he's cutting in, not reading)."""
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
                if catch_stop(speaking.is_set):  # he said "stop" while it was talking
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

        Everything queued goes out as ONE utterance, so a single stop silences all of it; he had to
        hit stop over and over while a report came at him line by line. It waits while he is
        recording, because it once broke in mid-sentence while he was talking. And if it's long it
        is OFFERED, not read out: a wall of an agent's own words is the thing he most wants to be
        insulated from.
        """
        if self._outbox is None or self._offered is not None or self._busy_recording():
            return
        messages = self._outbox.drain()
        if not messages:
            return
        news = "\n\n".join(messages)
        self._console.heads_up(news)  # shown in full, however it gets spoken
        if self._long_answer_chars is not None and len(news) > self._long_answer_chars:
            self._offered = news
            self._say(self.ready_question, record=False)
            return
        self._say(news, record=False)

    def _busy_recording(self):
        """Is he mid-utterance right now? A mic that reports it (the window's) is asked; anything
        else can't be interrupted mid-sentence anyway, because it only yields between turns."""
        busy = getattr(self._stt, "is_busy", None)
        return bool(busy and busy())

    def _think(self, heard):
        """Ask the brain off the main thread so a slow reply can't read as a crash. The first
        check-in comes after `patience`, then it keeps checking in every `check_in` seconds - each
        time saying how long it's been - until the reply lands. If he barges in while it's thinking,
        the call is cancelled and `_ThinkInterrupted` is raised so the loop drops the turn. If it
        runs past `detach_after`, it's handed to the background and `_ThinkDetached` is raised so the
        loop is freed and the answer is offered later. Re-raises whatever the brain raised, so the
        caller's error handling is unchanged."""
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
        # Listen for a spoken "stop" for the whole think, not just during a check-in - so he can cut
        # off a slow brain call by voice even in its silent stretches, the same as pressing Enter.
        stop_watching = self._watch_for_spoken_stop()
        self._floor_watched = stop_watching is not None
        try:
            next_check_in = start + self._patience
            detach_at = start + self._detach_after if self._detach_after is not None else None
            while not done.is_set():
                if self._interrupted():  # he cut in - cancel the call and abandon the turn
                    self._cancel_think(done)
                    raise _ThinkInterrupted
                if detach_at is not None and time.monotonic() >= detach_at:  # too slow - background it
                    self._speak_reply(self._detach_line())
                    self._detach(done, outcome)
                    raise _ThinkDetached
                deadline = next_check_in if detach_at is None else min(next_check_in, detach_at)
                timeout = min(self._interrupt_poll, max(0.0, deadline - time.monotonic()))
                if done.wait(timeout):
                    break
                now = time.monotonic()
                if now >= next_check_in:  # still thinking - tell him how long, then keep waiting
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
        calls run long doesn't repeat one canned sentence at him."""
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

    def _detach(self, done, outcome):
        """Leave the slow call running on its worker and remember it; a reaper breaks the next lull
        the moment it lands, so the finished answer gets offered promptly rather than waiting for him
        to speak first."""
        self._background = {"done": done, "outcome": outcome}
        if self._wake is not None:
            threading.Thread(target=self._reap, args=(done,), daemon=True).start()

    def _reap(self, done):
        done.wait()
        self._wake.set()  # break the mic's lull so the loop cycles round and offers the answer

    def _collect_background(self):
        """If a detached call has finished, take its answer and offer it (a failure is dropped - a
        background best-effort, not worth surfacing as a glitch). Runs at the top of a turn."""
        background = self._background
        if background is None or not background["done"].is_set():
            return
        self._background = None
        reply = background["outcome"].get("reply")
        if reply is not None and self._offered is None:
            self._offered = reply
            self._speak_reply(self.ready_question)

    def turn(self):
        if self._interrupt is not None:
            self._interrupt.clear()  # a fresh turn; forget any leftover "stop" from the last one
        self._deliver_outbox()  # say any queued agent news before we start listening again
        self._collect_background()  # a slow answer that has since landed is offered here
        if not self._paused:  # asleep it's only watching for the wake word - don't claim otherwise
            self._console.listening()
        heard = self._stt.listen()
        if not heard.strip():
            # An empty turn that still ended on "over" means he said only the terminator - let him
            # know it registered (the "✓ got it" cue already printed) instead of leaving dead air.
            if getattr(self._stt, "caught_terminator", False):
                self._say(self.empty_turn_reply)
            return None
        canonical = _canonical(heard)
        farewell = _ends_with_command(canonical, self._farewells)
        if self._paused and not farewell and not _wakes(canonical, self._resumes):
            # Asleep, and it's neither a wake word nor a goodbye - so it's the TV, or someone else in
            # the room. Don't transcribe it back at him; just show that it landed and was dropped.
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
        if self._offered is not None:  # he's answering "ready for it?" from a held long/slow reply
            return self._resolve_offer(heard)
        if self._background is not None:
            self._abandon_background()  # he's talking again - his live turn outranks the old call
        return self._answer(heard)

    def _abandon_background(self):
        """He spoke while a detached call was still running. There's only one session, so it can't
        answer him until that call ends - and bouncing him with a canned "still finishing your last
        one" threw his words away every time, which locked him out of the conversation entirely.
        Cancel the stale call instead: what he's saying now always outranks work he's given up on."""
        background = self._background
        self._background = None
        self._console.dropped()  # so the promise it made doesn't just silently evaporate
        self._cancel_think(background["done"])  # unwind it before his turn starts a new call

    def _answer(self, heard):
        """Acknowledge, think, and speak the reply - unless it's long enough to gate, in which case
        it's held and offered first (see _offer)."""
        self._say(self._acknowledgement)  # let him know he was heard before the thinking pause
        self._console.thinking()  # a "(thinking…)" indicator so a pause doesn't read as a hang
        think_start = time.monotonic()
        try:
            said = self._think(heard)
        except _ThinkInterrupted:  # he cut the thinking off - no reply, straight back to listening
            return None
        except _ThinkDetached:  # too slow - it's running in the background; offered when it lands
            return None
        except Exception as exc:  # surface the real cause instead of a silent "glitch"
            print(f"[brain error] {exc!r}", file=sys.stderr)
            self._speak_reply(self.error_reply)
            return Turn(heard=heard, said=self.error_reply, error=True)
        think_time = time.monotonic() - think_start
        if self._should_gate(said):
            return self._offer(heard, said)
        speak_start = time.monotonic()
        self._speak_reply(said)  # if he hit Enter while it was talking, this is cut off
        if self._timings:
            self._console.timing(think=think_time, speak=time.monotonic() - speak_start)
        self._pause_to_read()
        return Turn(heard=heard, said=said)

    def _should_gate(self, reply):
        return self._long_answer_chars is not None and len(reply) > self._long_answer_chars

    def _offer(self, heard, answer):
        """Hold a long answer and ask if he wants it, rather than dumping it. His next turn's yes
        releases it (see _resolve_offer)."""
        self._offered = answer
        self._speak_reply(self.ready_question)
        return Turn(heard=heard, said=self.ready_question)

    def _resolve_offer(self, heard):
        """His reply to "ready for it?": a yes speaks the held answer, a no drops it, and speech
        that answers NEITHER way - TV chatter, or him moving on to something else - is handled as an
        ordinary turn with the offer left standing, so noise can't destroy an answer he never got
        to accept or refuse."""
        if _is_affirmative(heard):
            answer, self._offered = self._offered, None
            self._speak_reply(answer)
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
