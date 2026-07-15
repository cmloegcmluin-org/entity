"""Run the Entity: `python -m entity` (speak to it).

  --text      type instead of speaking
  --mute      show replies as text, don't speak them
  --timings   print how long each turn spends thinking vs. speaking
"""

import sys
import time

from entity.brain_sdk import SdkBrain
from entity.conversation import Conversation
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


def _build_ears(text_mode):
    """Return (stt, mic) — mic is None in text mode, otherwise an open stream to close on exit."""
    if text_mode:
        return ConsoleSTT(), None
    from entity.mic import Microphone
    from entity.stt_mic import MicSTT
    from entity.transcribe import ParakeetTranscriber

    transcriber = ParakeetTranscriber()
    transcriber.warmup()  # load the 2.4 GB model now, not on the first spoken turn
    mic = Microphone()
    return MicSTT(transcriber, mic), mic


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    text_mode = "--text" in argv
    muted = "--mute" in argv
    timings = "--timings" in argv

    print("Entity is waking up...")
    brain = SdkBrain()
    brain.warmup()
    stt, mic = _build_ears(text_mode)
    tts = NullTTS() if muted else SystemTTS(rate=2)

    if timings:
        brain.respond = _timed(brain.respond, "think")
        tts.speak = _timed(tts.speak, "speak")

    entry = "Type to talk" if text_mode else "Speak when you see '(listening...)'"
    print(f"Entity is here. {entry}; say 'goodbye entity' or press Ctrl-C to end.")
    if muted:
        print("(muted: replies are shown, not spoken)")
    print()

    def show(turn):
        if not text_mode:
            print(f"you said: {turn.heard}")
        print(f"entity> {turn.said}\n")

    try:
        Conversation(stt, brain, tts).run(on_turn=show)
    except KeyboardInterrupt:
        print("\nTalk soon.")
    finally:
        brain.close()
        if mic is not None:
            mic.close()


if __name__ == "__main__":
    main()
