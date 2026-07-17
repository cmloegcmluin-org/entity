"""Run the Entity: `python -m entity` (speak to it).

  --text      type instead of speaking
  --mute      show replies as text, don't speak them
  --timings   print how long each turn spends thinking vs. speaking
"""

import signal
import sys
import threading
import time
from pathlib import Path

from entity.brain_sdk import DEFAULT_PERSONA, SdkBrain
from entity.conversation import Conversation
from entity.fleet_io import ConsoleFleetIO, VoiceFleetIO
from entity.inbox_watcher import InboxWatcher
from entity.memory import (
    CONSOLIDATION_PROMPT,
    append_learned,
    compose_persona,
    load_learned,
    load_profile,
    parse_facts,
)
from entity.outbox import Outbox
from entity.startup import ScriptedFirstTurn, load_startup_instructions
from entity.stt_console import ConsoleSTT
from entity.supervising_brain import SupervisingBrain
from entity.tts_system import NullTTS, SystemTTS

RUNTIME_DIR = Path(__file__).resolve().parents[2] / "runtime"
STARTUP_INSTRUCTIONS = RUNTIME_DIR / "startup-instructions.txt"
AGENT_INBOX = RUNTIME_DIR / "agent-inbox"  # agents drop questions/review-ready notes here, one per line


def _agent_inbox_note(inbox):
    """Persona line telling the Entity how its agents reach the user - the exact absolute path, since
    the agents run in other projects' worktrees and can't guess where the Entity keeps its inbox."""
    return (
        " When you put a background agent on a task, tell that agent - in its own instructions - to "
        f"write anything it needs from the user (a question, or that it's ready for review) as a single "
        f"line to {inbox}\\<a-short-agent-name>.txt. the user can't watch the agents' screens, so that "
        "inbox is the only way he hears from them - always set it up when you delegate."
    )


def _timed(call, label):
    def wrapped(arg):
        start = time.perf_counter()
        try:
            return call(arg)
        finally:
            print(f"  [{label} {time.perf_counter() - start:.1f}s]", file=sys.stderr)

    return wrapped


def _build_ears(text_mode, stop, interrupt):
    """Return (stt, mic, recorder) — mic/recorder are None in text mode; both close on exit.
    `interrupt` lets a quiet moment be broken off so the Entity can pass on queued agent news."""
    if text_mode:
        return ConsoleSTT(), None, None
    from datetime import datetime

    from entity.mic import Microphone
    from entity.recorder import AudioRecorder
    from entity.stt_mic import MicSTT
    from entity.transcribe import ParakeetTranscriber

    transcriber = ParakeetTranscriber()
    transcriber.warmup()  # load the 2.4 GB model now, not on the first spoken turn
    mic = Microphone()
    recorder = AudioRecorder(RUNTIME_DIR / "audio" / f"session-{datetime.now():%Y%m%d-%H%M%S}.wav")
    print(f"(saving your audio to {recorder.path} - nothing you say gets lost, even on a crash)")
    cue = lambda: print("  ✓ got it", flush=True)  # visual "registered" the instant you say "over"
    stt = MicSTT(transcriber, mic, stop=stop, cue=cue, recorder=recorder, interrupt=interrupt)
    return stt, mic, recorder


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    text_mode = "--text" in argv
    muted = "--mute" in argv
    timings = "--timings" in argv

    # Shutdown is driven by one stop flag. The reliable trigger is a spoken/typed farewell
    # ("goodbye entity", "quit") which the transcriber always catches; Enter (stdin watcher)
    # and Ctrl-C (SIGINT handler) also set it, but both are flaky under Git Bash's terminal.
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    # Word from the agents the Entity drives lands in this inbox; the watcher tails it and the
    # Entity speaks each new line at the next lull (never cutting the user off).
    AGENT_INBOX.mkdir(parents=True, exist_ok=True)
    outbox = Outbox()
    inbox_watcher = InboxWatcher(AGENT_INBOX, outbox)
    inbox_watcher.start()

    print("Entity is waking up...")
    persona = compose_persona(DEFAULT_PERSONA, load_profile(), load_learned()) + _agent_inbox_note(AGENT_INBOX)
    brain = SdkBrain(persona=persona)
    brain.warmup()
    stt, mic, recorder = _build_ears(text_mode, stop, outbox.arrived)

    # Standing kickoff: whatever he's dropped in the startup-instructions file becomes his first
    # turn automatically, so he never retypes the same long instructions to get going.
    first = load_startup_instructions(STARTUP_INSTRUCTIONS)
    if first is not None:
        print(f"(read your startup instructions from {STARTUP_INSTRUCTIONS})")
    else:
        print(f"(tip: drop standing startup instructions in {STARTUP_INSTRUCTIONS} to skip retyping them)")
    stt = ScriptedFirstTurn(stt, first)

    tts = NullTTS() if muted else SystemTTS(rate=2)

    # Driving a fleet is just something you ask the Entity to do in conversation: this wrapper
    # catches a "[SUPERVISE] ..." directive from the brain and runs the agents through the same voice.
    fleet_io = ConsoleFleetIO() if text_mode else VoiceFleetIO(speak=tts.speak, listen=stt.listen)
    brain = SupervisingBrain(brain, fleet_io)

    if timings:
        brain.respond = _timed(brain.respond, "think")
        tts.speak = _timed(tts.speak, "speak")

    if not text_mode:
        threading.Thread(target=lambda: (sys.stdin.readline(), stop.set()), daemon=True).start()

    if text_mode:
        print("Entity is here. Type to talk; say 'quit' or 'goodbye entity' to end.")
    else:
        print("Entity is here. Speak, and say 'over' when you finish each turn.")
        print("To end, say 'goodbye entity over' (or press Enter).")
    if muted:
        print("(muted: replies are shown, not spoken)")
    print()

    if not text_mode and not muted:
        tts.speak("I'm ready. What's on your mind?")  # say out loud that startup finished

    had_conversation = []

    def show(turn):
        had_conversation.append(True)
        if not text_mode:
            print(f"you said: {turn.heard}")
        print(f"entity> {turn.said}\n")

    try:
        Conversation(stt, brain, tts, outbox=outbox).run(
            should_continue=lambda: not stop.is_set(), on_turn=show
        )
    except KeyboardInterrupt:
        stop.set()
    finally:
        inbox_watcher.stop()
        if stop.is_set():
            try:
                tts.speak("Talk soon.")  # farewell words already speak their own goodbye
            except Exception:
                pass
        print("Talk soon.")
        if had_conversation:  # ask the brain what it learned and remember it for next time
            try:
                append_learned(parse_facts(brain.respond(CONSOLIDATION_PROMPT)))
            except Exception:
                pass
        for closer in (
            brain.close,
            mic.close if mic is not None else None,
            recorder.close if recorder is not None else None,
        ):
            try:
                if closer is not None:
                    closer()
            except Exception:
                pass


if __name__ == "__main__":
    main()
