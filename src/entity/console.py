"""What the Entity shows in the terminal, kept separate from what it speaks.

The spoken word is transient - it's gone the moment it's said. The terminal is where the user can
read the reply back, see that it's thinking rather than hung, and catch an unprompted heads-up. One
seam for all of it keeps the conversation loop about flow, not print formatting, and makes the
terminal easy to silence or capture (tests, or a text-mode run that shouldn't echo his own typing).
"""


def _print_flushed(line):
    # Flush so the "(thinking…)" indicator actually appears while it thinks, not after.
    print(line, flush=True)


class Console:
    def __init__(self, *, echo=_print_flushed, show_heard=True, thinking_notice="(thinking…)"):
        self._echo = echo
        self._show_heard = show_heard
        self._thinking_notice = thinking_notice

    def heard(self, text):
        if self._show_heard:  # off in text mode - he can see what he typed
            self._echo(f"you said: {text}")

    def thinking(self):
        self._echo(self._thinking_notice)

    def reply(self, text):
        self._echo(f"entity> {text}\n")  # trailing blank line separates turns in the transcript

    def heads_up(self, text):
        self._echo(f"entity (heads-up)> {text}\n")  # marked so an unprompted line isn't mistaken for a reply

    def timing(self, *, think, speak):
        self._echo(f"  [think {think:.1f}s · speak {speak:.1f}s]")  # the --timings per-turn readout
