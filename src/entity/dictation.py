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

While the Entity itself is speaking, the Conversation runs `catch_stop` - chunks heard then are
checked for a stop BARK (and never drafted, since they're mostly its own voice): the same barge-in
as the terminal mode.
"""

import queue
import threading

import numpy as np

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
    ):
        self._transcriber = transcriber
        self._mic = mic
        self._on_draft = on_draft
        self._on_state = on_state
        self._on_level = on_level
        self._on_submit_request = on_submit_request
        self._recording = not muted
        self._terminator = terminator
        self._mutes = tuple(canonical(p) for p in mute_phrases)
        self._wakes = tuple(canonical(p) for p in wake_phrases)
        self._pause_frames = pause_frames
        self._stop = stop
        self._interrupt = interrupt
        self._submitted = queue.SimpleQueue()  # the window hands finished turns over here
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
        try:
            while active():
                if bark.wait(0.05):
                    return True
            return False
        finally:
            self._bark = None

    # ---- the window-facing half ----------------------------------------------------------------

    def set_recording(self, recording):
        """The mic button (and the spoken phrases) - flips the state and tells the window."""
        self._recording = recording
        self._on_state("recording" if recording else "muted")

    def start(self):
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
            if self._stop is not None and self._stop.is_set():
                return
            level = rms(frame)
            self._on_level(level if self._recording else 0.0)
            speech = floor.is_speech(level)
            if not started:
                if not speech:
                    continue
                started = True
            chunk.append(frame)
            silence = 0 if speech else silence + 1
            if silence >= self._pause_frames:  # a burst ended - transcribe just that burst
                self._absorb(np.concatenate(chunk))
                chunk, silence, started = [], 0, False
        if chunk:  # a finite source ran out mid-burst (a real mic never does)
            self._absorb(np.concatenate(chunk))

    def _absorb(self, audio):
        text = self._transcriber.transcribe(audio).strip()
        if not text:
            return
        if self._bark is not None:  # the Entity is talking - this is a bark check, never draft text
            if _is_stop_bark(text, STOP_WORDS):
                self._bark.set()
            return
        if self._recording:
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
