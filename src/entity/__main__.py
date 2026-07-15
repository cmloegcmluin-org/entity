"""Run the Entity: `python -m entity` (speak to it).

  --text      type instead of speaking
  --mute      show replies as text, don't speak them
  --timings   print how long each turn spends thinking vs. speaking
"""

import signal
import sys
import threading
import time

from entity.brain_sdk import DEFAULT_PERSONA, SdkBrain
from entity.conversation import Conversation
from entity.profile import compose_persona, load_profile
from entity.stt_console import ConsoleSTT
from entity.tts_system import NullTTS, SystemTTS


def _timed(call, label):
    def wrapped(arg):
        start = time.perf_counter()
        try:
            return call(arg)
        finally:
            print(f"  [{label} {time.perf_counter() - start:.1f}s]", file=sys.stderr)

    return wrapped


def _build_ears(text_mode, stop):
    """Return (stt, mic) — mic is None in text mode, otherwise an open stream to close on exit."""
    if text_mode:
        return ConsoleSTT(), None
    from entity.mic import Microphone
    from entity.stt_mic import MicSTT
    from entity.transcribe import ParakeetTranscriber

    transcriber = ParakeetTranscriber()
    transcriber.warmup()  # load the 2.4 GB model now, not on the first spoken turn
    mic = Microphone()
    return MicSTT(transcriber, mic, stop=stop), mic


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

    print("Entity is waking up...")
    brain = SdkBrain(persona=compose_persona(DEFAULT_PERSONA, load_profile()))
    brain.warmup()
    stt, mic = _build_ears(text_mode, stop)
    tts = NullTTS() if muted else SystemTTS(rate=2)

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

    def show(turn):
        if not text_mode:
            print(f"you said: {turn.heard}")
        print(f"entity> {turn.said}\n")

    try:
        Conversation(stt, brain, tts).run(should_continue=lambda: not stop.is_set(), on_turn=show)
    except KeyboardInterrupt:
        stop.set()
    finally:
        if stop.is_set():
            try:
                tts.speak("Talk soon.")  # farewell words already speak their own goodbye
            except Exception:
                pass
        print("Talk soon.")
        for closer in (brain.close, mic.close if mic is not None else None):
            try:
                if closer is not None:
                    closer()
            except Exception:
                pass


if __name__ == "__main__":
    main()
