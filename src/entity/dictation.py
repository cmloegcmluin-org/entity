"""The window's microphone: continuous dictation into an editable draft, no "over" required.

The walkie-talkie model (talk, say "over", the turn is sent) made him repeat himself and forced
every thought to be final. In the window the mic is a STATE, not a turn: while it's on, everything
he says is transcribed chunk-by-chunk into the draft box - which he can edit - and nothing is sent
until he clicks Submit (or says "over", which still works). "Stop listening" turns the mic off
mid-stream and keeps the words before it; "hey Entity" turns it back on and keeps the words after
it; the window's button does the same by hand. Muted, the room is heard but dropped - only the
wake phrase gets through.

The pump runs on its own thread for the whole session. It reports through callbacks (draft text,
mic state, level for the meter, submit requests) so the window can mirror it without this module
knowing anything about Tk. `listen()` is the Conversation-facing half: it blocks until the window
hands over the (possibly edited) draft via `submit`, so the loop's think/speak flow is unchanged.

Two things decide whether he is being heard: whether the mic is ARMED (his button, his spoken
phrases) and whether the Entity is SPEAKING. It only takes dictation when armed and not speaking -
because a mic that is live while the Entity talks hears the Entity: his very first draft box opened
with "I do for you", the tail of its own spoken greeting. Chunks heard while it speaks are checked
for a stop BARK instead, which is the barge-in. Arming survives a reply, so a conversation flows
without touching the button; only cutting it off mid-sentence disarms, since a stop should not turn
straight around and start recording his next breath.
"""

import queue
import threading

import numpy as np

from entity.hesitation import without_hesitations
from entity.phrases import canonical, ends_with_command, strip_leading_command, wakes
from entity.stt_mic import (
    PAUSE_FRAMES,
    STOP_WORDS,
    NoiseFloor,
    _is_backchannel,
    _is_stop_bark,
    _strip_terminator,
    rms,
)

DEFAULT_MUTE_PHRASES = ("stop listening", "suspend")
DEFAULT_WAKE_PHRASES = ("hey entity", "resume")


class Dictation:
    def __init__(
        self,
        transcriber,
        mic,
        *,
        on_draft,
        on_state,
        on_level,
        on_submit_request,
        muted=False,
        terminator="over",
        mute_phrases=DEFAULT_MUTE_PHRASES,
        wake_phrases=DEFAULT_WAKE_PHRASES,
        pause_frames=PAUSE_FRAMES,
        stop=None,
        interrupt=None,
        recorder=None,
    ):
        self._transcriber = transcriber
        self._mic = mic
        self._on_draft = on_draft
        self._on_state = on_state
        self._on_level = on_level
        self._on_submit_request = on_submit_request
        self._armed = not muted
        self._speaking = False
        self._terminator = terminator
        self._mutes = tuple(canonical(p) for p in mute_phrases)
        self._wakes = tuple(canonical(p) for p in wake_phrases)
        self._pause_frames = pause_frames
        self._stop = stop
        self._interrupt = interrupt
        self._recorder = recorder
        self._submitted = queue.SimpleQueue()  # the window hands finished turns over here
        self._mid_burst = False  # he is talking right now: a burst has started and not yet ended
        self._finish_burst = False  # muted mid-sentence: take down what's still in the air
        self._bark = None  # while the Entity speaks: an Event a stop bark should fire

    # ---- the Conversation-facing half ----------------------------------------------------------

    def listen(self):
        """Block until the window submits a turn (Submit button, or a spoken "over"). An interrupt
        (queued agent news during a lull) yields "" so the loop can go deliver it."""
        while True:
            if self._stop is not None and self._stop.is_set():
                return ""
            if self._interrupt is not None and self._interrupt.is_set():
                return ""
            try:
                return self._submitted.get(timeout=0.1)
            except queue.Empty:
                continue

    def submit(self, text):
        """The window hands over the draft - as edited, which is the whole point of the box."""
        self._submitted.put(text.strip())

    def catch_stop(self, active, words=STOP_WORDS):
        """While the Entity talks, chunks become bark-checks instead of draft text. True the moment
        he barks a stop; False once `active()` goes false (the reply finished on its own)."""
        bark = threading.Event()
        self._bark = bark
        self.begin_speaking()
        try:
            while active():
                if bark.wait(0.05):
                    return True
            return False
        finally:
            self._bark = None
            self.end_speaking()

    # ---- the window-facing half ----------------------------------------------------------------

    def set_recording(self, recording):
        """The mic button (and the spoken phrases) - arms or disarms and tells the window.

        Disarming mid-sentence keeps that sentence: he said a whole one, pressed mic-off, and the
        words never arrived, because the burst was still buffered waiting for a pause. Turning the
        mic off means "stop listening from here", not "throw away the part you hadn't written down".
        """
        if not recording and self._armed and self._mid_burst:
            self._finish_burst = True
        self._armed = recording
        self._announce_state()

    def is_recording(self):
        """Is his mic live? The loop asks before ever speaking up on its own: while the mic is on,
        he may be talking, and it once broke in mid-sentence."""
        return self._armed

    def begin_speaking(self):
        """The Entity has started talking: stop taking dictation until it's done."""
        self._speaking = True
        self._announce_state()

    def end_speaking(self):
        """It has finished (or been cut off) - back to however he had left the mic."""
        self._speaking = False
        self._announce_state()

    def hearing_him(self):
        return self._armed and not self._speaking

    def _announce_state(self):
        self._on_state("speaking" if self._speaking else ("recording" if self._armed else "muted"))

    def start(self):
        self._announce_state()  # the window opened before this existed; tell it how the mic stands
        thread = threading.Thread(target=self.pump, daemon=True)
        thread.start()
        return thread

    def pump(self):
        """The forever loop: frames in, draft text / state changes / levels / submits out. Runs on
        its own thread against a real mic; tests run it inline against a finite one."""
        floor = NoiseFloor()
        chunk = []
        silence = 0
        started = False
        for frame in self._mic.frames():
            if self._recorder is not None:
                self._recorder.write(frame)  # to disk first, so a crash can't lose what he said
            if self._stop is not None and self._stop.is_set():
                return
            level = rms(frame)
            self._on_level(level if self.hearing_him() else 0.0)
            speech = floor.is_speech(level)
            if not started:
                if not speech:
                    continue
                started = True
            chunk.append(frame)
            silence = 0 if speech else silence + 1
            ended = silence >= self._pause_frames  # he paused: that burst is over
            if ended or self._finish_burst:  # ...or he muted while still mid-sentence
                self._absorb(np.concatenate(chunk), armed=self._armed or self._finish_burst)
                self._finish_burst = False
                chunk, silence, started = [], 0, False
            self._mid_burst = started
        if chunk:  # a finite source ran out mid-burst (a real mic never does)
            self._absorb(np.concatenate(chunk), armed=self._armed or self._finish_burst)
            self._finish_burst = False
        self._mid_burst = False

    def _absorb(self, audio, *, armed=None):
        armed = self._armed if armed is None else armed
        # The "um"s and "uh"s go before anything reads the text - the same rule Notecraft uses on
        # his memos, so both hear him the same way.
        text = without_hesitations(self._transcriber.transcribe(audio).strip())
        if not text:
            return
        if self._speaking:  # its own voice, mostly - a bark check, never draft text
            if self._bark is not None and _is_stop_bark(text, STOP_WORDS):
                self._bark.set()
            return
        if armed:
            self._take_dictation(text)
        else:
            self._maybe_wake(text)

    def _take_dictation(self, text):
        spoken = canonical(text)
        if ends_with_command(spoken, self._mutes):
            self._draft_before_mute(text, spoken)
            self.set_recording(False)
            return
        without_over = _strip_terminator(text, self._terminator)
        if without_over is not None:
            if without_over and not _is_backchannel(without_over, self._terminator):
                self._on_draft(without_over)
            self._on_submit_request()  # "over" still submits - old muscle memory, same meaning
            return
        if _is_backchannel(text, self._terminator):
            return  # Parakeet's hallucinated filler on near-silence, not him
        self._on_draft(text)

    def _draft_before_mute(self, text, spoken):
        """He said something and THEN the mute phrase ("add eggs, stop listening") - keep the
        something; the phrase itself never belongs in the draft."""
        for phrase in self._mutes:
            if spoken != phrase and spoken.endswith(" " + phrase):
                kept = text.strip()[: -len(phrase)].rstrip(" ,.;:!?-")
                if kept and not _is_backchannel(kept, self._terminator):
                    self._on_draft(kept)
                return

    def _maybe_wake(self, text):
        spoken = canonical(text)
        if not wakes(spoken, self._wakes):
            return  # muted: the room talks, nothing gets through but the wake phrase
        self.set_recording(True)
        rest = strip_leading_command(spoken, self._wakes)
        if rest:  # "hey entity add milk" - the wake phrase carried his first real words
            self._on_draft(rest)
