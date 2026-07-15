"""Run the Entity: `python -m entity`.

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


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    muted = "--mute" in argv
    timings = "--timings" in argv

    stt = ConsoleSTT()
    print("Entity is waking up...")
    brain = SdkBrain()
    brain.warmup()
    tts = NullTTS() if muted else SystemTTS(rate=2)

    if timings:
        brain.respond = _timed(brain.respond, "think")
        tts.speak = _timed(tts.speak, "speak")

    print("Entity is here. Type to talk; say 'goodbye entity' or press Ctrl-D to end.")
    if muted:
        print("(muted: replies are shown, not spoken)")
    print()

    def show(turn):
        print(f"entity> {turn.said}\n")

    try:
        Conversation(stt, brain, tts).run(on_turn=show)
    except KeyboardInterrupt:
        print("\nTalk soon.")
    finally:
        brain.close()


if __name__ == "__main__":
    main()
