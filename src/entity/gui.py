"""A window for the Entity, so a session doesn't have to live in a terminal.

The window is a mirror, not a second implementation: the Console drives the same two seams it
drives for a terminal (whole lines, and in-place overwrites for the ignore counter), and those ops
flow through a thread-safe feed into a pure line model the Tk widget re-renders from. Everything
that can be wrong lives in the feed and the model, which need no display to test; the tkinter layer
just mirrors `model.lines` and forwards two buttons.

Threads: the conversation loop runs on a worker thread and pushes into the feed; tkinter runs the
main thread and polls the feed with `after`, so no Tk call ever happens off the Tk thread.
"""

import queue


class TranscriptModel:
    """The lines the window shows. Pure: ops in, lines out - the widget just mirrors `lines`.

    An "overwrite" op is the carriage-return trick the terminal uses for the ignore counter: the
    first one opens a live line, later ones replace it in place, and a bare newline closes it."""

    def __init__(self):
        self.lines = []
        self._live = False  # an overwrite run is standing open at lines[-1]

    def apply(self, op, text):
        if op == "overwrite":
            self._overwrite(text)
            return
        self._live = False
        self.lines.extend(str(text).split("\n"))

    def _overwrite(self, text):
        if text == "\n":  # the run is being closed; its final count stays as an ordinary line
            self._live = False
            return
        body = text.lstrip("\r")
        if self._live:
            self.lines[-1] = body
        else:
            self.lines.append(body)
            self._live = True


class TranscriptFeed:
    """Thread-safe hand-off from the conversation loop to the window."""

    def __init__(self):
        self._ops = queue.SimpleQueue()

    def push(self, op, text):
        self._ops.put((op, text))

    def drain(self):
        ops = []
        while True:
            try:
                ops.append(self._ops.get_nowait())
            except queue.Empty:
                return ops


class EntityWindow:
    """The actual Tk window: a transcript pane and a Stop button.

    Kept deliberately thin - rendering is "mirror model.lines", input is "Stop sets the barge-in".
    `on_close` fires when he closes the window; the caller stops the conversation, and calls
    `close_when(done)` so the window ends once the worker has wound down."""

    POLL_MS = 80

    def __init__(self, feed, *, on_stop, on_close, title="Entity"):
        import tkinter as tk
        from tkinter import scrolledtext

        self._feed = feed
        self._model = TranscriptModel()
        self._shown = 0  # how many model lines the widget already has
        self._done = None  # once set (close_when), the poll loop ends the window when it fires
        self.ended = False  # True once the window has destroyed itself

        self._tk = tk.Tk()
        self._tk.title(title)
        self._tk.geometry("720x560")
        self._text = scrolledtext.ScrolledText(self._tk, wrap="word", state="disabled",
                                               font=("Consolas", 11))
        self._text.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        stop = tk.Button(self._tk, text="STOP - cut it off", height=2, command=on_stop)
        stop.pack(fill="x", padx=8, pady=(0, 8))
        self._tk.bind("<Return>", lambda event: on_stop())  # Enter keeps meaning what it meant

        def closed():
            on_close()  # ask the conversation to stop; the poll loop ends the window once it has

        self._tk.protocol("WM_DELETE_WINDOW", closed)

    def close_when(self, done):
        """Once `done` (a threading.Event) fires, the window shuts itself on its own thread."""
        self._done = done

    def _drain_once(self):
        """Pull pending ops into the model and mirror any change into the widget. Called from the
        Tk thread (the poll loop) - and directly by tests, which is why it's separate from run()."""
        changed = False
        for op, text in self._feed.drain():
            self._model.apply(op, text)
            changed = True
        if changed or self._shown != len(self._model.lines):
            self._render()
        if self._done is not None and self._done.is_set():
            self.destroy()

    def _render(self):
        # A session's transcript is small; rewriting the whole widget keeps overwrite-runs trivial.
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._text.insert("end", "\n".join(self._model.lines))
        self._text.configure(state="disabled")
        self._text.see("end")
        self._shown = len(self._model.lines)

    def _poll(self):
        self._drain_once()
        self._tk.after(self.POLL_MS, self._poll)

    def run(self):
        self._poll()
        self._tk.mainloop()

    def withdraw(self):
        """Hide the window (tests render into it without flashing anything on screen)."""
        self._tk.withdraw()

    def widget_text(self):
        return self._text.get("1.0", "end-1c")

    def destroy(self):
        if not self.ended:  # idempotent - the poll loop and a test teardown may both get here
            self.ended = True
            self._tk.destroy()
