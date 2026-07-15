"""Run the Entity: `python -m entity` (add --mute for text-only, no speaking)."""

import sys

from entity.brain_claude import ClaudeBrain
from entity.conversation import Conversation
from entity.stt_console import ConsoleSTT
from entity.tts_system import NullTTS, SystemTTS


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    muted = "--mute" in argv

    convo = Conversation(
        ConsoleSTT(),
        ClaudeBrain(),
        NullTTS() if muted else SystemTTS(),
    )

    print("Entity is here. Type to talk; say 'goodbye entity' or press Ctrl-D to end.")
    if muted:
        print("(muted: replies are shown, not spoken)")
    print()

    def show(turn):
        print(f"entity> {turn.said}\n")

    try:
        convo.run(on_turn=show)
    except KeyboardInterrupt:
        print("\nTalk soon.")


if __name__ == "__main__":
    main()
