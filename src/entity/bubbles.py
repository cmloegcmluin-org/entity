"""Message bubbles: each message a real tinted box, sided and sized like a text thread.

Why a widget per message and not a text tag: a Tk text tag paints its background across the whole
line box, edge to edge, however the margins are set - so tagging could wrap the WORDS into a
column while the tint stayed a stripe the width of the window. A bubble has to be a box whose
width is its own, which means a real widget. Each one is a `Frame` of measured size holding a
`Text` that can still be selected and copied from, embedded in the pane and pushed to its side
by the line's justify.

The measuring is pure and lives at the top of this file, so what decides a bubble's width can be
tested without a display; the Tk half below only builds what those numbers describe.
"""

from entity.theme import DIM, FG, ITS, MINE, PAST, SELECTION

SHARE = 0.55  # of the pane a bubble may take - about half, the way a message thread reads
PAD_X = 10
PAD_Y = 6
NAME_FONT = ("Segoe UI", 8)

# Who sits where, and in what tint. A heads-up is Entity talking out of turn, so it takes Entity's
# side and color and says so in the name.
SIDES = {"you": ("right", MINE), "entity": ("left", ITS), "heads-up": ("left", ITS)}


def size_bubble(text, pane_width, measure, line_height):
    """The lines a bubble holds and the pixel box they need.

    A short message gets a short box - the tint hugs the words, as in any message thread - and a
    long one stops at its share of the pane instead of running the width of the window."""
    limit = max(60, int(pane_width * SHARE) - 2 * PAD_X)
    lines = wrap_to_pixels(text, limit, measure)
    width = max(measure(line) for line in lines) + 2 * PAD_X
    return lines, width, len(lines) * line_height + 2 * PAD_Y


def wrap_to_pixels(text, limit, measure):
    """Break `text` into lines none wider than `limit`, asking `measure` what a string costs.

    Pixels, not characters, because the pane is set in a proportional font: an "i" and a "W" cost
    wildly different amounts, so a character count is not a width. A word with no break in it -
    a pasted URL - is cut mid-word rather than allowed to push the bubble past its half."""
    lines = []
    for paragraph in text.split("\n"):
        started = len(lines)
        for word in paragraph.split():
            if len(lines) > started and measure(f"{lines[-1]} {word}") <= limit:
                lines[-1] = f"{lines[-1]} {word}"
            else:
                lines.extend(_break_word(word, limit, measure))
        if len(lines) == started:
            lines.append("")  # a blank line that was typed is a blank line
    return lines


def _break_word(word, limit, measure):
    """A word that fits, as one line; one that doesn't, in as many pieces as it takes."""
    pieces, piece = [], ""
    for letter in word:
        if piece and measure(piece + letter) > limit:
            pieces.append(piece)
            piece = ""
        piece += letter
    return pieces + [piece]


class Thread:
    """The messages rendered into one pane: a name line, then a bubble, per message.

    Owns every bubble it made, so a resize can re-measure them and a copy can read them back - a
    pane's own `get` returns nothing for an embedded window, so the pane is no longer where the
    conversation's text lives.
    """

    FALLBACK_WIDTH = 900  # what to assume before the window has been laid out even once

    def __init__(self, pane, names, *, prepare):
        from tkinter import font as tkfont

        self._pane = pane
        self._names = names
        self._prepare = prepare  # makes a bubble read-only and copyable, the way panes are
        self._font = tkfont.Font(font=pane.cget("font"))
        self._shown = []  # [(entry, holder or None, body or None)]
        self._fitted = None  # the pane width the boxes were last measured for
        for role, (side, _) in SIDES.items():
            pane.tag_configure(role, justify=side)
            pane.tag_configure(f"{role}:name", justify=side, foreground=DIM, font=NAME_FONT,
                               spacing1=8)
        pane.tag_configure("status", justify="center", foreground=DIM, font=NAME_FONT,
                           spacing1=4, spacing3=4)
        pane.tag_configure("historical", foreground=PAST)
        # Its own pane, not the window: a tab not yet opened is sized only when it appears,
        # and the window sends no resize of its own for that.
        pane.bind("<Configure>", lambda event: self.refit())

    @property
    def pane(self):
        return self._pane

    def show(self, entry):
        """Append one message - or, for a status line, one centred remark with no bubble."""
        import tkinter as tk

        role, faded = entry["role"], ("historical",) if entry["historical"] else ()
        if role not in SIDES:
            self._pane.insert("end", entry["text"] + "\n", ("status",) + faded)
            self._shown.append((entry, None, None))
            return
        self._pane.insert("end", self._name_line(entry) + "\n", (f"{role}:name",) + faded)
        colour = SIDES[role][1]
        holder = tk.Frame(self._pane, bg=colour)
        holder.pack_propagate(False)  # the box is the size we measured, not the size of its text
        body = tk.Text(holder, bg=colour, fg=PAST if faded else FG, font=self._font, wrap="none",
                       borderwidth=0, highlightthickness=0, padx=PAD_X, pady=PAD_Y,
                       selectbackground=SELECTION, insertwidth=0)
        body.pack(fill="both", expand=True)
        self._prepare(body)
        # The pointer rests over a bubble nearly all the time and a Text swallows the wheel, so
        # hand it up to the pane - or the conversation stops scrolling wherever the pointer is.
        body.bind("<MouseWheel>", self._wheel)
        start = self._pane.index("end-1c")
        self._pane.window_create("end", window=holder, pady=1)
        self._pane.insert("end", "\n")
        self._pane.tag_add(role, start, "end-1c")  # justify puts the box on its side of the pane
        self._shown.append((entry, holder, body))
        self._fill(self._shown[-1])

    def refit(self):
        """Re-measure every bubble for the pane's width. A box fixed in pixels stops being half of
        anything the moment the window's edge is dragged."""
        if self._width() != self._fitted:
            for shown in self._shown:
                if shown[1] is not None:
                    self._fill(shown)

    def text(self):
        """The conversation as it would want to be pasted - the words as said, not the wrapped lines."""
        return "".join(f"{self._name_line(entry)}\n{entry['text']}\n" if holder is not None
                       else f"{entry['text']}\n" for entry, holder, _ in self._shown)

    def geometry(self):
        """Where the bubbles actually landed: (role, x, width) each, measured off the widgets."""
        return [(entry["role"], holder.winfo_x(), holder.winfo_width())
                for entry, holder, _ in self._shown if holder is not None]

    def bodies(self):
        """The widgets holding each message's words - what a selection is made in."""
        return [body for _, holder, body in self._shown if holder is not None]

    def _fill(self, shown):
        entry, holder, body = shown
        lines, width, height = size_bubble(entry["text"], self._width(), self._font.measure,
                                           self._font.metrics("linespace"))
        holder.configure(width=width, height=height)
        body.delete("1.0", "end")
        body.insert("end", "\n".join(lines))
        self._fitted = self._width()

    def _name_line(self, entry):
        name = self._names.get(entry["role"], f"{self._names['entity']} · heads-up")
        return f"{name} · {entry['stamp']}"

    def _width(self):
        """The pane's usable width - its own padding is not room a bubble can have."""
        width = self._pane.winfo_width()
        if width <= 1:  # not laid out yet; corrected by the first resize
            return self.FALLBACK_WIDTH
        return width - 2 * int(self._pane.cget("padx"))

    def _wheel(self, event):
        self._pane.yview_scroll(-event.delta // 120, "units")
        return "break"
