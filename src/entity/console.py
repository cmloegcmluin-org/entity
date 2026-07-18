"""What the Entity shows in the terminal, kept separate from what it speaks.

The spoken word is transient - it's gone the moment it's said. The terminal is where the user can
read the reply back, see that it's thinking rather than hung, and catch an unprompted heads-up. One
seam for all of it keeps the conversation loop about flow, not print formatting, and makes the
terminal easy to silence or capture (tests, or a text-mode run that shouldn't echo his own typing).
"""

import sys


def _print_flushed(line):
    # Flush so the "(thinking…)" indicator actually appears while it thinks, not after.
    print(line, flush=True)


def _overwrite_flushed(text):
    # Written as-is, with no trailing newline - so text that starts with a carriage return lands
    # back on top of the line just written. That's what collapses a run of ignores onto one line.
    sys.stdout.write(text)
    sys.stdout.flush()


class Console:
    def __init__(self, *, echo=_print_flushed, overwrite=_overwrite_flushed, show_heard=True,
                 thinking_notice="(thinking…)"):
        self._echo = echo
        self._overwrite = overwrite
        self._show_heard = show_heard
        self._thinking_notice = thinking_notice
        self._ignored = 0  # length of the current run of ignored utterances, collapsed onto one line

    def ignored(self):
        """It heard something while asleep and dropped it. A TV in the room can produce these all
        evening, so the run collapses onto a single line whose count ticks up, rather than scrolling
        his terminal away."""
        self._ignored += 1
        tally = f" {self._ignored}x" if self._ignored > 1 else ""
        self._overwrite(f"\r(ignoring…{tally})")

    def _line(self, text):
        """Every ordinary line goes through here so it can first close an open ignore run - without
        that newline it would be written on top of the counter, which is still sitting unterminated."""
        if self._ignored:
            self._ignored = 0
            self._overwrite("\n")
        self._echo(text)

    def heard(self, text):
        if self._show_heard:  # off in text mode - he can see what he typed
            self._line(f"you said: {text}")

    def thinking(self):
        self._line(self._thinking_notice)

    def reply(self, text):
        self._line(f"entity> {text}\n")  # trailing blank line separates turns in the transcript

    def heads_up(self, text):
        self._line(f"entity (heads-up)> {text}\n")  # marked so an unprompted line isn't mistaken for a reply

    def timing(self, *, think, speak):
        self._line(f"  [think {think:.1f}s · speak {speak:.1f}s]")  # the --timings per-turn readout
