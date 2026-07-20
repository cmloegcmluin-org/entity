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

from entity.theme import DIM, FG, ITS, MINE, PANEL, PAST, SELECTION

SHARE = 0.55  # of the pane a bubble may take - about half, the way a message thread reads
PAD_X = 10
PAD_Y = 6
NAME_FONT = ("Segoe UI", 8)
PAGE = 40  # messages built at a time - what a screenful needs, and what a scroll back adds
COPY_ICON = "⧉"  # two joined squares - the copy glyph, in the font the window already uses
REACH_MS = 250  # how long the copy button waits after the pointer leaves, to be reachable

# Who sits where, and in what tint. A heads-up is Entity talking out of turn, so it takes Entity's
# side and color and says so in the name.
SIDES = {"you": ("right", MINE), "entity": ("left", ITS), "heads-up": ("left", ITS)}


def hold_back(entries, *, already, page=PAGE):
    """Split a batch into what waits and what is built now, as (waiting, building).

    Every session ever recorded arrives in one batch when the window opens, and a bubble is two
    real widgets - a thousand of them costs a second, and the archive only ever grows. So the
    window opens on the live end and the rest is built as it is scrolled back to. Only that
    first batch is ever held: anything arriving later is newer than what is already up, and
    prepending it would put it above messages it came after."""
    if already or len(entries) <= page:
        return [], entries
    return entries[:-page], entries[-page:]


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


def _remembering(measure):
    """Asking Tk the width of a string is a round-trip into Tcl, and wrapping asks thousands of
    times - the same words over and over, since it is one conversation. Remember the answers."""
    known = {}

    def width(text):
        if text not in known:
            known[text] = measure(text)
        return known[text]

    return width


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
    conversation's text lives. It also owns the ones it has NOT made: the archive going back to
    the first session is held as entries and built a page at a time as it is scrolled up into.
    """

    FALLBACK_WIDTH = 900  # what to assume before the window has been laid out even once
    KEPT = "kept-the-place"  # marks the line being read, while more of the past loads above it

    def __init__(self, pane, names, *, prepare):
        from tkinter import font as tkfont

        self._pane = pane
        self._names = names
        self._prepare = prepare  # makes a bubble read-only and copyable, the way panes are
        self._font = tkfont.Font(font=pane.cget("font"))
        self._measure = _remembering(self._font.measure)
        self._shown = []  # [(entry, holder or None, body or None)], oldest built first
        self._waiting = []  # older entries, held until they are scrolled back to
        self._growing = False  # building prepends, which scrolls, which would build again
        self._fitted = None  # the pane width the boxes were last measured for
        self._marks = {}  # id(entry) -> a Tk mark on its line, for the ones with no widget
        self._offered = None  # the entry the copy button currently belongs to
        self._withdrawing = None  # a pending hide, cancelled if the pointer reaches the button
        self._copied = None  # the text the copy button last handed over
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
        # Chained, not replaced - the scrollbar still needs telling where it is. This is how we
        # hear that the top has been reached and more of the past is wanted than is built.
        pane.configure(yscrollcommand=lambda first, last: self._scrolled(pane.vbar, first, last))
        self._copier = self._build_copier(pane)

    def _build_copier(self, pane):
        """ONE copy button, moved to whatever the pointer is over. One per message would be
        thousands of widgets built for the archive, for a thing only ever visible in one place."""
        import tkinter as tk

        copier = tk.Label(pane, text=COPY_ICON, font=NAME_FONT, bg=PANEL, fg=DIM,
                          cursor="hand2", padx=4, pady=1)
        copier.bind("<Button-1>", lambda event: self._take_copy())
        # Reaching for the button leaves the message, and a hide on that would take the button
        # away before it could be clicked - so the hide waits, and arriving here cancels it.
        copier.bind("<Enter>", lambda event: self._keep_offered())
        copier.bind("<Leave>", lambda event: self._withdraw_copy())
        copier.bind("<MouseWheel>", self._wheel)
        return copier

    @property
    def pane(self):
        return self._pane

    def show(self, entries):
        """Build a batch at the bottom, holding back all but the newest page of the first one."""
        held, building = hold_back(entries, already=len(self._shown))
        self._waiting.extend(held)  # extended, never replaced, or the first live message after
        for entry in building:      # the archive loads would leave all of it unreachable
            self._build(entry)

    def grow(self):
        """Build the next page back, above what is up - what scrolling to the top asked for.

        A mark holds the line being read, because everything lands ABOVE it: without that the
        view stays pinned to the top of the pane, which both loses the reader's place and reads
        as still being at the top, so the page after it loads immediately too."""
        page, self._waiting = self._waiting[-PAGE:], self._waiting[:-PAGE]
        self._growing = True
        self._pane.mark_set(self.KEPT, "@0,0")
        try:
            for entry in reversed(page):  # each goes above the last, so they end up in order
                self._build(entry, prepend=True)
        finally:
            self._growing = False
        self._pane.see(self.KEPT)

    def waiting(self):
        """How much of the past is held but not built yet."""
        return len(self._waiting)

    def reveal(self, entry):
        """Scroll to `entry`, building back through the held past until it exists to scroll to."""
        while any(held is entry for held in self._waiting):
            self.grow()
        for shown, holder, _ in self._shown:
            if shown is entry:
                self._pane.see(holder if holder is not None else self._marks[id(entry)])
                return

    # ---- the copy button -------------------------------------------------------------------

    def _offer_copy(self, entry):
        """Put the copy button beside whatever the pointer is over."""
        if self._withdrawing is not None:
            self._pane.after_cancel(self._withdrawing)
            self._withdrawing = None
        self._offered = entry
        spot = self._beside_bubble(entry) if entry["role"] in SIDES else self._beside_line(entry)
        if spot is None:
            self._copier.place_forget()
            return
        # `place` measures from inside the pane's own padding while `winfo_x` and `bbox` report
        # from outside it, so a position worked out from those lands one padding to the right -
        # over the edge of a right-hand bubble, which is the side the button sits against.
        pad = int(self._pane.cget("padx"))
        self._copier.place(x=spot[0] - pad, y=spot[1] - int(self._pane.cget("pady")), anchor="w")
        self._copier.lift()

    def _keep_offered(self):
        if self._withdrawing is not None:
            self._pane.after_cancel(self._withdrawing)
            self._withdrawing = None

    def _withdraw_copy(self):
        """Take it away - but not instantly, or reaching for it is what removes it."""
        if self._withdrawing is None:
            self._withdrawing = self._pane.after(REACH_MS, self._copier.place_forget)

    def _take_copy(self):
        """What was hovered, on the clipboard: one message, or a whole session."""
        entry = self._offered
        if entry is None:
            return
        text = entry["text"] if entry["role"] in SIDES else self.session_text(entry)
        self._copied = text  # which text was handed over, for a test to check without the OS
        self._pane.clipboard_clear()
        self._pane.clipboard_append(text)

    def _beside_bubble(self, entry):
        """Just outside the bubble's near edge, so it never sits over the words."""
        for shown, holder, _ in self._shown:
            if shown is entry and holder is not None:
                self._copier.update_idletasks()
                width = self._copier.winfo_reqwidth()
                if SIDES[entry["role"]][0] == "right":
                    return holder.winfo_x() - width - 6, holder.winfo_y() + holder.winfo_height() // 2
                return (holder.winfo_x() + holder.winfo_width() + 6,
                        holder.winfo_y() + holder.winfo_height() // 2)
        return None

    def _beside_line(self, entry):
        """Just right of a centred break's own text, rather than out at the pane's edge."""
        mark = self._marks.get(id(entry))
        if mark is None:
            return None
        box = self._pane.bbox(f"{mark} lineend -1c")
        if box is None:
            return None  # scrolled out of view; there is nothing to sit beside
        x, y, width, height = box
        return x + width + 8, y + height // 2

    def session_text(self, entry):
        """Everything said in the session this break opens, up to where the next one starts."""
        every = self._waiting + [shown for shown, _, _ in self._shown]
        try:
            start = next(index for index, held in enumerate(every) if held is entry)
        except StopIteration:
            return ""
        said = []
        for following in every[start + 1:]:
            if following["role"] == "session":
                break
            said.append(self._as_text(following))
        return "".join(said)

    def _scrolled(self, bar, first, last):
        bar.set(first, last)
        if self._waiting and not self._growing and float(first) <= 0.01:
            self.grow()

    def _build(self, entry, *, prepend=False):
        """One message: a name line and a bubble, at the bottom or above everything."""
        import tkinter as tk

        role, faded = entry["role"], ("historical",) if entry["historical"] else ()
        if role not in SIDES:
            self._build_line(entry, prepend, faded)
            return
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
        named = (f"{role}:name",) + faded
        if prepend:  # bottom-up, since each insert at 1.0 pushes the last one down
            self._pane.insert("1.0", "\n")
            self._pane.window_create("1.0", window=holder, pady=1)
            self._pane.tag_add(role, "1.0", "2.0")
            self._pane.insert("1.0", self._name_line(entry) + "\n", named)
        else:
            self._pane.insert("end", self._name_line(entry) + "\n", named)
            start = self._pane.index("end-1c")
            self._pane.window_create("end", window=holder, pady=1)
            self._pane.insert("end", "\n")
            self._pane.tag_add(role, start, "end-1c")  # justify puts the box on its side
        shown = (entry, holder, body)
        for hoverable in (holder, body):
            hoverable.bind("<Enter>", lambda event, e=entry: self._offer_copy(e))
            hoverable.bind("<Leave>", lambda event: self._withdraw_copy())
        self._remember(shown, prepend)
        self._fill(shown)

    def _build_line(self, entry, prepend, faded):
        """A centred remark with no bubble - a status line, a day, or a session break.

        Every one gets a mark, because a line has no widget to scroll to the way a bubble does,
        and the contents list scrolls to the line a session opens with. A session break also
        gets a tag, so hovering it can offer to copy the whole session."""
        where = "1.0" if prepend else "end"
        start = "1.0" if prepend else self._pane.index("end-1c")
        self._pane.insert(where, entry["text"] + "\n", ("status",) + faded)
        mark = f"line{id(entry)}"  # no hyphen: a mark name is parsed as part of an index expression
        self._pane.mark_set(mark, start)
        self._pane.mark_gravity(mark, "left")
        self._marks[id(entry)] = mark
        if entry["role"] == "session":
            tag = f"break-{id(entry)}"
            self._pane.tag_add(tag, start, f"{start} lineend")
            self._pane.tag_bind(tag, "<Enter>", lambda event, e=entry: self._offer_copy(e))
            self._pane.tag_bind(tag, "<Leave>", lambda event: self._withdraw_copy())
        self._remember((entry, None, None), prepend)

    def _remember(self, shown, prepend):
        if prepend:
            self._shown.insert(0, shown)
        else:
            self._shown.append(shown)

    def refit(self):
        """Re-measure every bubble for the pane's width. A box fixed in pixels stops being half of
        anything the moment the window's edge is dragged."""
        if self._width() != self._fitted:
            for shown in self._shown:
                if shown[1] is not None:
                    self._fill(shown)

    def text(self):
        """The conversation as it would want to be pasted - the words as said, not the wrapped
        lines, and all of them, including the past not yet scrolled back to."""
        every = self._waiting + [entry for entry, _, _ in self._shown]
        return "".join(self._as_text(entry) for entry in every)

    def _as_text(self, entry):
        if entry["role"] not in SIDES:
            return f"{entry['text']}\n"
        return f"{self._name_line(entry)}\n{entry['text']}\n"

    def geometry(self):
        """Where the bubbles actually landed: (role, x, width) each, measured off the widgets."""
        return [(entry["role"], holder.winfo_x(), holder.winfo_width())
                for entry, holder, _ in self._shown if holder is not None]

    def bodies(self):
        """The widgets holding each message's words - what a selection is made in."""
        return [body for _, holder, body in self._shown if holder is not None]

    def hover_gap(self, index):
        """Hover one bubble and measure how far clear of it the copy button lands. Negative would
        mean the button sitting over the message it offers to copy."""
        entry, holder, body = [shown for shown in self._shown if shown[1] is not None][index]
        body.event_generate("<Enter>")
        self._pane.update_idletasks()
        if SIDES[entry["role"]][0] == "right":
            return holder.winfo_x() - (self._copier.winfo_x() + self._copier.winfo_width())
        return self._copier.winfo_x() - (holder.winfo_x() + holder.winfo_width())

    def hover_copies(self, index):
        """Hover one bubble, press its copy button, and hand back the text that copied.

        The text it handed over - not what the system clipboard holds afterwards. Every Windows
        clipboard read and write needs the machine-wide clipboard lock, and any other process
        holding it for an instant (clipboard history, another app) makes Tk's update silently do
        nothing. Read back, that left the PREVIOUS run's text sitting there looking exactly like a
        copy that had just happened, and about one suite run in ten died on it. Which text this
        window hands over is its behaviour; whether Windows was free to take it is not."""
        self._copied = None
        body = self.bodies()[index]
        body.event_generate("<Enter>")
        self._copier.event_generate("<Button-1>")
        self._pane.update()
        return self._copied

    def _fill(self, shown):
        entry, holder, body = shown
        lines, width, height = size_bubble(entry["text"], self._width(), self._measure,
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
