"""What the Entity shows, kept separate from what it speaks.

The spoken word is transient - it's gone the moment it's said. A surface the user can read is where
he catches up on the reply, sees it's thinking rather than hung, and notices an unprompted heads-up.
One seam for all of it keeps the conversation loop about flow rather than formatting, and lets the
same session drive a terminal, a window, or nothing at all (tests, a typed run that shouldn't echo
his own words back at him).

Three outputs, because they answer different questions: `echo`/`overwrite` paint a terminal,
`record` keeps the durable session file, and `messages` reports WHO said each line for a surface
that renders a conversation instead of a log - so the window never re-parses the prefixes written
right here.
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
    def __init__(self, *, echo=_print_flushed, overwrite=_overwrite_flushed, record=None,
                 messages=None, voice=True, thinking_notice="(thinking…)",
                 listening_notice="(listening… say 'over' when you're done)"):
        self._echo = echo
        self._overwrite = overwrite
        # Where the same lines go to be kept - the terminal scrolls away, and it was the only record
        # of what he actually saw when something went wrong.
        self._record = record or (lambda line: None)
        # Who said each line, for a conversation view. Empty for a terminal, which shows prefixes.
        self._messages = messages or (lambda role, text: None)
        # A voice run narrates the mic - "listening", and what it heard. A typed run needs neither:
        # he has his own prompt and his own words on screen already.
        self._voice = voice
        self._thinking_notice = thinking_notice
        self._listening_notice = listening_notice
        self._ignored = 0  # length of the current run of ignored utterances, collapsed onto one line

    def listening(self):
        # An empty notice says nothing: the window has a mic button and a level meter, so
        # "(listening… say 'over' when you're done)" would be both wrong there and noise.
        if self._voice and self._listening_notice:
            self._line(self._listening_notice)

    def ignored(self):
        """It heard something while asleep and dropped it. A TV in the room can produce these all
        evening, so the run collapses onto a single line whose count ticks up, rather than scrolling
        his terminal away."""
        self._ignored += 1
        tally = f" {self._ignored}x" if self._ignored > 1 else ""
        self._overwrite(f"\r(ignoring…{tally})")

    def _line(self, text, *, show=True):
        """Every ordinary line goes through here so it can first close an open ignore run - without
        that newline it would be written on top of the counter, which is still sitting unterminated.
        A line that isn't shown is still kept: the record is of the session, not of the screen."""
        if self._ignored:
            self._record(f"(ignored {self._ignored} while asleep)")  # the tally, not every scrap
            self._ignored = 0
            self._overwrite("\n")
        if show:
            self._echo(text)
        self._record(text)

    def heard(self, text):
        self._line(f"you said: {text}", show=self._voice)
        self._messages("you", text)

    def thinking(self):
        self._line(self._thinking_notice)
        self._messages("status", self._thinking_notice)

    def reply(self, text):
        self._line(f"entity> {text}\n")  # trailing blank line separates turns in the transcript
        self._messages("entity", text)

    def spoke(self, text):
        """Something he HEARD that the terminal deliberately doesn't show - the acknowledgement, the
        still-working check-ins. It still belongs in the record: reading a session back and seeing no
        check-ins made it look like none had fired, when he had actually heard every one."""
        self._line(text, show=False)
        self._messages("entity", text)  # he heard it, so a conversation view shows it

    def dropped(self):
        """A long call that had been left running was cancelled so he could be answered instead.
        He was promised an answer that then never came; at minimum the record says why."""
        self._line("(dropped the long call that was still running)")
        self._messages("status", "(dropped the long call that was still running)")

    def heads_up(self, text):
        self._line(f"entity (heads-up)> {text}\n")  # marked so an unprompted line isn't mistaken for a reply
        self._messages("heads-up", text)

    def timing(self, *, think, speak):
        self._line(f"  [think {think:.1f}s · speak {speak:.1f}s]")  # the --timings per-turn readout
