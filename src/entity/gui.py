"""A window for the Entity, so a session doesn't have to live in a terminal.

The window is a mirror, not a second implementation: the Console drives the same seams it drives
for a terminal (whole lines, in-place overwrites), the Dictation reports through its callbacks
(draft text, mic state, levels, submit requests), and everything flows through one thread-safe
feed into the Tk thread. The logic that can be wrong - the line model, the feed, section parsing,
log tailing - lives outside Tk and needs no display to test; the tkinter layer mirrors state and
forwards buttons.

Layout, dark throughout: a top bar with the mic toggle, a live level meter and STOP; a notebook
with the conversation (past sessions preloaded above a divider), one tab per agent log tailed
live, and Goals / Projects / Enhancements rendered straight from the profile - so a filed
[IMPROVE] shows up on its tab within a poll; and below, the editable draft the dictation types
into, with Submit.

Threads: the conversation loop and the dictation pump run on workers and push into the feed;
tkinter runs the main thread and polls with `after`, so no Tk call ever happens off the Tk thread.
"""

import queue
from pathlib import Path

from entity.memory import profile_sections
from entity.tailing import LogTail, discover

BG = "#161616"
PANEL = "#1f1f1f"
FG = "#d6d6d6"
DIM = "#8a8a8a"
ACCENT = "#7fff00"  # teal - his color
LEVEL_FULL = 0.06  # the meter tops out around his loud speech, so ordinary talk visibly moves it


class TranscriptModel:
    """The lines the conversation pane shows. Pure: ops in, lines out - the widget mirrors `lines`.

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
    """Thread-safe hand-off from the conversation loop and the dictation pump to the window."""

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
    """The actual Tk window. Kept thin: rendering mirrors the model and the profile file; input
    forwards to callbacks (`on_stop`, `on_close`, `on_submit`, `on_mic`). `close_when(done)` lets
    the window end itself once the conversation has wound all the way down."""

    POLL_MS = 80
    SLOW_POLL_EVERY = 12  # agent logs and the profile re-check ~once a second, not every 80ms

    def __init__(self, feed, *, on_stop, on_close, on_submit=None, on_mic=None,
                 profile_path=None, agent_logs_dir=None, icon=None, title="Entity"):
        import tkinter as tk
        from tkinter import scrolledtext, ttk

        self._tk_module = tk
        self._feed = feed
        self._model = TranscriptModel()
        self._shown = 0
        self._done = None
        self.ended = False
        self._on_submit = on_submit or (lambda text: None)
        self._on_mic = on_mic or (lambda recording: None)
        self._recording = True
        self._profile_path = Path(profile_path) if profile_path else None
        self._profile_stamp = None
        self._agent_logs_dir = Path(agent_logs_dir) if agent_logs_dir else None
        self._tails = {}  # agent name -> (LogTail, text widget)
        self._section_tabs = {}  # section tab label -> text widget
        self._polls = 0

        self._tk = tk.Tk()
        self._tk.title(title)
        self._tk.geometry("860x680")
        self._tk.configure(bg=BG)
        if icon and Path(icon).exists():
            try:
                self._tk.iconbitmap(str(icon))
            except Exception:
                pass  # a bad icon must never keep the window from opening

        bar = tk.Frame(self._tk, bg=BG)
        bar.pack(fill="x", padx=8, pady=(8, 4))
        self._mic_button = tk.Button(bar, text="", width=14, command=self._toggle_mic,
                                     bg=PANEL, fg=ACCENT, activebackground=PANEL,
                                     activeforeground=ACCENT, relief="flat", cursor="hand2")
        self._mic_button.pack(side="left")
        self._level = tk.Canvas(bar, width=180, height=16, bg=PANEL, highlightthickness=0)
        self._level.pack(side="left", padx=8)
        self._level_bar = self._level.create_rectangle(0, 0, 0, 16, fill=ACCENT, width=0)
        tk.Button(bar, text="STOP", command=on_stop, bg="#5a1f1f", fg="#ffb3b3",
                  activebackground="#5a1f1f", activeforeground="#ffb3b3",
                  relief="flat", width=10, cursor="hand2").pack(side="right")
        self._show_state("recording")

        style = ttk.Style(self._tk)
        style.theme_use("clam")
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL, foreground=FG, padding=(10, 4))
        style.map("TNotebook.Tab", background=[("selected", "#333333")],
                  foreground=[("selected", ACCENT)])
        self._tabs = ttk.Notebook(self._tk)
        self._tabs.pack(fill="both", expand=True, padx=8, pady=4)

        self._text = scrolledtext.ScrolledText(self._tabs, wrap="word", state="disabled",
                                               font=("Consolas", 11), **self._pane_colors())
        self._tabs.add(self._text, text="Conversation")
        for label in ("Goals", "Projects", "Enhancements"):
            pane = scrolledtext.ScrolledText(self._tabs, wrap="word", state="disabled",
                                             font=("Consolas", 11), **self._pane_colors())
            self._tabs.add(pane, text=label)
            self._section_tabs[label] = pane

        draft_row = tk.Frame(self._tk, bg=BG)
        draft_row.pack(fill="x", padx=8, pady=(0, 8))
        self._draft = tk.Text(draft_row, height=4, wrap="word", font=("Consolas", 11),
                              insertbackground=ACCENT, **self._pane_colors())
        self._draft.pack(side="left", fill="both", expand=True)
        tk.Button(draft_row, text="Submit", command=self._submit, bg="#2a4d00", fg=ACCENT,
                  activebackground="#2a4d00", activeforeground=ACCENT,
                  relief="flat", width=10, cursor="hand2").pack(side="right", fill="y", padx=(6, 0))
        self._tk.bind("<Return>", lambda event: on_stop())  # Enter still cuts the voice off

        self._tk.protocol("WM_DELETE_WINDOW", on_close)

    def _pane_colors(self):
        return dict(bg=PANEL, fg=FG, selectbackground="#3a5f00", borderwidth=0)

    # ---- input, Tk thread ----------------------------------------------------------------------

    def _toggle_mic(self):
        self._recording = not self._recording
        self._show_state("recording" if self._recording else "muted")
        self._on_mic(self._recording)

    def _submit(self):
        text = self._draft.get("1.0", "end-1c").strip()
        self._draft.delete("1.0", "end")
        if text:
            self._on_submit(text)

    # ---- rendering, Tk thread ------------------------------------------------------------------

    def _show_state(self, state):
        self._recording = state == "recording"
        if self._recording:
            self._mic_button.configure(text="● listening", fg=ACCENT)
        else:
            self._mic_button.configure(text="○ muted", fg=DIM)
            self._level.coords(self._level_bar, 0, 0, 0, 16)

    def _show_level(self, level):
        width = int(min(1.0, level / LEVEL_FULL) * 180)
        self._level.coords(self._level_bar, 0, 0, width, 16)

    def _append_draft(self, text):
        current = self._draft.get("1.0", "end-1c")
        if current and not current.endswith((" ", "\n")):
            text = " " + text
        self._draft.insert("end", text)
        self._draft.see("end")

    def _drain_once(self):
        changed = False
        level = None
        for op, text in self._feed.drain():
            if op == "level":
                level = text  # only the newest matters; drawing each would just flicker
            elif op == "state":
                self._show_state(text)
            elif op == "draft":
                self._append_draft(text)
            elif op == "submit":
                self._submit()
            else:
                self._model.apply(op, text)
                changed = True
        if level is not None and self._recording:
            self._show_level(level)
        if changed or self._shown != len(self._model.lines):
            self._render()
        self._polls += 1
        if self._polls % self.SLOW_POLL_EVERY == 0:
            self._refresh_agent_tabs()
            self._refresh_profile_tabs()
        if self._done is not None and self._done.is_set():
            self.destroy()

    def _render(self):
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._text.insert("end", "\n".join(self._model.lines))
        self._text.configure(state="disabled")
        self._text.see("end")
        self._shown = len(self._model.lines)

    def _refresh_agent_tabs(self):
        if self._agent_logs_dir is None:
            return
        from tkinter import scrolledtext

        for name in discover(self._agent_logs_dir):
            if name not in self._tails:
                pane = scrolledtext.ScrolledText(self._tabs, wrap="word", state="disabled",
                                                 font=("Consolas", 10), **self._pane_colors())
                self._tabs.add(pane, text=f"⚙ {name}")
                self._tails[name] = (LogTail(self._agent_logs_dir / f"{name}.log"), pane)
        for tail, pane in self._tails.values():
            new = tail.poll()
            if new:
                pane.configure(state="normal")
                pane.insert("end", new)
                pane.configure(state="disabled")
                pane.see("end")

    def _refresh_profile_tabs(self):
        if self._profile_path is None:
            return
        try:
            stamp = self._profile_path.stat().st_mtime
        except OSError:
            return
        if stamp == self._profile_stamp:
            return
        self._profile_stamp = stamp
        sections = profile_sections(self._profile_path.read_text(encoding="utf-8"))
        for label, pane in self._section_tabs.items():
            # tab label "Enhancements" matches the heading that STARTS with it, however long the
            # heading runs on ("Enhancements he wants for you (roadmap, not now)").
            body = next((text for heading, text in sections.items()
                         if heading.lower().startswith(label.lower())), "(nothing here yet)")
            pane.configure(state="normal")
            pane.delete("1.0", "end")
            pane.insert("end", body)
            pane.configure(state="disabled")

    # ---- lifecycle -----------------------------------------------------------------------------

    def close_when(self, done):
        """Once `done` (a threading.Event) fires, the window shuts itself on its own thread."""
        self._done = done

    def _poll(self):
        self._drain_once()
        if not self.ended:
            self._tk.after(self.POLL_MS, self._poll)

    def run(self):
        self._poll()
        self._tk.mainloop()

    def withdraw(self):
        """Hide the window (tests render into it without flashing anything on screen)."""
        self._tk.withdraw()

    def widget_text(self):
        return self._text.get("1.0", "end-1c")

    def draft_text(self):
        return self._draft.get("1.0", "end-1c")

    def tab_labels(self):
        return [self._tabs.tab(tab_id, "text") for tab_id in self._tabs.tabs()]

    def section_text(self, label):
        return self._section_tabs[label].get("1.0", "end-1c")

    def agent_tab_text(self, name):
        return self._tails[name][1].get("1.0", "end-1c")

    def destroy(self):
        if not self.ended:  # idempotent - the poll loop and a test teardown may both get here
            self.ended = True
            self._tk.destroy()
