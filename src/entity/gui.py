"""A window for the Entity, so a session doesn't have to live in a terminal.

The window is a mirror, not a second implementation: the Console reports each line and WHO said it,
the Dictation reports draft text, mic state and levels, and everything flows through one thread-safe
feed into the Tk thread. What can be wrong - the message model, the feed, section parsing, log
tailing - lives outside Tk and is tested without a display; the tkinter layer mirrors state and
forwards buttons.

Layout: the conversation reads like a text thread - his messages on the right, Entity's on the
left, each with a name and a time, past sessions above a divider. Everything to do with him talking
sits together along the bottom: mic button, level meter, the editable draft his speech types into,
and Submit. Beside the conversation are tabs - one per agent log, tailed live, and his Goals,
Projects and Enhancements, editable and saved straight back into the profile the brain reads.

Threads: the conversation loop and the dictation pump run on workers and push into the feed;
tkinter runs the main thread and polls with `after`, so no Tk call ever happens off the Tk thread.
"""

import queue
import time
from pathlib import Path

from entity.memory import profile_sections, save_section
from entity.tailing import LogTail, discover
from entity.transcript import parse_line

BG = "#161616"
PANEL = "#1f1f1f"
FG = "#d6d6d6"
DIM = "#7a7a7a"
HIS = "#2b3a1a"  # his messages, right - a warm dark green
ITS = "#22262b"  # Entity's, left - a cool dark slate
ACCENT = "#7fff00"  # teal
LEVEL_FULL = 0.06  # the meter tops out around his loud speech, so ordinary talk visibly moves it

# Windows groups taskbar buttons by AppUserModelID, and a process that declares none inherits the
# identity of whatever other python-hosted app already owns a button: the Entity window turned up
# under his SidebarTool icon, wearing SidebarTool's icon. Declaring one gives it its own.
APP_ID = "the user.Entity"


def _set_app_id_via_shell32(app_id):
    import ctypes

    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)


def set_app_id(app_id, api=_set_app_id_via_shell32):
    """Claim a taskbar identity. Must happen before the window exists, and a platform without the
    API just doesn't get one - a cosmetic nicety must never keep the window from opening."""
    try:
        api(app_id)
    except Exception:
        pass


def _clock():
    return time.strftime("%H:%M:%S")


class TranscriptModel:
    """The conversation the window shows: entries of {role, stamp, text, historical}.

    Pure - ops in, entries out - so every rendering decision can be tested without a display. An
    "overwrite" op is the carriage-return trick the terminal uses for the ignore counter: it opens
    a live status entry, and later ones replace it in place.
    """

    def __init__(self, clock=_clock):
        self.entries = []
        self._clock = clock
        self._counter = None  # index of the live ignore-counter entry, if one is open

    def apply(self, op, payload):
        if op == "message":
            role, text = payload
            self._add(role, text)
        elif op == "history":
            parsed = parse_line(payload)
            if parsed is not None:
                role, stamp, text = parsed
                self._add(role, text, stamp=stamp, historical=True)
        elif op == "line":
            if str(payload).strip():
                self._add("status", str(payload))
        elif op == "overwrite":
            self._overwrite(payload)

    def _add(self, role, text, *, stamp=None, historical=False):
        self._counter = None
        self.entries.append({
            "role": role,
            "stamp": stamp or self._clock(),
            "text": text.strip(),
            "historical": historical,
        })

    def _overwrite(self, text):
        if text == "\n":  # the run is being closed; its final count stays as an ordinary entry
            self._counter = None
            return
        body = text.lstrip("\r")
        if self._counter is not None:
            self.entries[self._counter]["text"] = body
            return
        self._add("status", body)
        self._counter = len(self.entries) - 1


class TranscriptFeed:
    """Thread-safe hand-off from the conversation loop and the dictation pump to the window."""

    def __init__(self):
        self._ops = queue.SimpleQueue()

    def push(self, op, payload):
        self._ops.put((op, payload))

    def drain(self):
        ops = []
        while True:
            try:
                ops.append(self._ops.get_nowait())
            except queue.Empty:
                return ops


class EntityWindow:
    """The Tk window. Thin on purpose: rendering mirrors the model and the profile file, input
    forwards to callbacks (`on_stop`, `on_close`, `on_submit`, `on_mic`). `close_when(done)` lets
    the window end itself once the conversation has wound all the way down."""

    POLL_MS = 80
    SLOW_POLL_EVERY = 12  # agent logs and the profile re-check ~once a second, not every 80ms
    AUTOSAVE_AFTER = 1.5  # seconds of not typing before an edited section is written back
    # His own five categories, in his numbering. Slot 1 was "Entity construction", which is
    # sufficiently done, so the conversation itself takes that number. The heading each tab reads
    # is explicit, since his profile calls category 3 "Life context (for awareness; ...)".
    SECTION_TABS = (
        ("2 · Enhancements", "Enhancements"),
        ("3 · Context", "Life context"),
        ("4 · Goals", "Goals"),
        ("5 · Projects", "Projects"),
    )
    CONVERSATION_TAB = "1 · Conversation"

    def __init__(self, feed, *, on_stop, on_close, on_submit=None, on_mic=None,
                 profile_path=None, agent_logs_dir=None, icon=None, title="Entity", clock=_clock,
                 now=time.monotonic):
        import tkinter as tk
        from tkinter import ttk

        self._feed = feed
        self._model = TranscriptModel(clock=clock)
        self._rendered = 0
        self._done = None
        self.ended = False
        self._on_stop = on_stop
        self._on_submit = on_submit or (lambda text: None)
        self._on_mic = on_mic or (lambda recording: None)
        self._state = "muted"  # the mic starts off; nothing is heard until he turns it on
        self._now = now
        self._profile_path = Path(profile_path) if profile_path else None
        self._profile_stamp = None
        self._agent_logs_dir = Path(agent_logs_dir) if agent_logs_dir else None
        self._tails = {}  # agent name -> (LogTail, pane, rendered-entry count)
        self._agent_models = {}  # agent name -> TranscriptModel of that exchange
        self._sections = {}  # tab label -> {"pane": Text, "heading": str, "edited": float|None}
        self._polls = 0
        self._clock = clock
        self._speaker_names = {"you": "You", "entity": "Entity"}

        set_app_id(APP_ID)  # before the window exists, or the taskbar button is already grouped
        self._tk = tk.Tk()
        self._tk.title(title)
        self._tk.geometry("980x760")
        self._tk.configure(bg=BG)
        if icon and Path(icon).exists():
            try:
                self._tk.iconbitmap(str(icon))
            except Exception:
                pass  # a bad icon must never keep the window from opening

        self._style_tabs(ttk)
        self._tabs = ttk.Notebook(self._tk)
        self._tabs.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        self._text = self._make_pane(self._tabs, readonly=True, font=("Segoe UI", 11))
        self._tabs.add(self._text, text=self.CONVERSATION_TAB)
        self._build_message_tags()
        for label, heading in self.SECTION_TABS:
            self._tabs.add(self._make_section_tab(tk, label, heading), text=label)
        # One home for the agents, however many he ends up driving at once.
        self._agent_tabs = ttk.Notebook(self._tabs)
        self._tabs.add(self._agent_tabs, text="Agents")
        self._build_controls(tk)
        self._tk.protocol("WM_DELETE_WINDOW", on_close)

    # ---- construction ---------------------------------------------------------------------------

    def _style_tabs(self, ttk):
        style = ttk.Style(self._tk)
        style.theme_use("clam")
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL, foreground=DIM, padding=(12, 6))
        # Told apart by its lighter panel and plain bright text - no colour, and no resizing: clam
        # grows/shrinks the selected tab by default, which left the active one a different height
        # from its neighbours.
        style.map("TNotebook.Tab", background=[("selected", "#333333")], foreground=[("selected", FG)],
                  expand=[("selected", [0, 0, 0, 0])], padding=[("selected", (12, 6))])

    def _make_pane(self, parent, *, readonly, font=("Consolas", 11)):
        """A pane he can always select and copy from. Read-only panes stay in the NORMAL state and
        refuse edits key by key instead - a disabled Text widget takes no focus, so Ctrl-C never
        reaches it and he cannot get his own conversation out of the window."""
        from tkinter import scrolledtext

        pane = scrolledtext.ScrolledText(
            parent, wrap="word", font=font, bg=PANEL, fg=FG, selectbackground="#3a5f00",
            borderwidth=0, padx=10, pady=8, insertbackground=ACCENT,
        )
        if readonly:
            pane.bind("<Key>", self._refuse_edit)
            pane.configure(insertwidth=0)  # no caret, so it reads as text rather than a box to type in
        return pane

    @staticmethod
    def _refuse_edit(event):
        """Swallow anything that would type into a read-only pane, while leaving selection,
        scrolling and copying alone."""
        allowed = {"Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next"}
        if event.keysym in allowed or (event.state & 0x4 and event.keysym.lower() in {"c", "a"}):
            return None
        return "break"

    def _build_message_tags(self, pane=None):
        pane = pane or self._text
        wide, narrow = 190, 12
        for role, colour, side in (("you", HIS, "right"), ("entity", ITS, "left"),
                                   ("heads-up", ITS, "left")):
            near, far = (wide, narrow) if side == "right" else (narrow, wide)
            pane.tag_configure(role, justify=side, background=colour, foreground=FG,
                               lmargin1=near, lmargin2=near, rmargin=far, spacing1=2, spacing3=6)
            pane.tag_configure(f"{role}:name", justify=side, foreground=DIM,
                               font=("Segoe UI", 8), lmargin1=near, lmargin2=near,
                               rmargin=far, spacing1=8)
        pane.tag_configure("status", justify="center", foreground=DIM, font=("Segoe UI", 8),
                           spacing1=4, spacing3=4)
        pane.tag_configure("historical", foreground="#8f8f8f")

    def _make_section_tab(self, tk, label, heading):
        """His own documents, edited in place - no Save button, because a document you have to
        remember to save is one you lose."""
        pane = self._make_pane(self._tabs, readonly=False)
        pane.bind("<KeyRelease>", lambda event, name=label: self._mark_dirty(name))
        self._sections[label] = {"pane": pane, "heading": heading, "edited": None}
        return pane

    def _build_controls(self, tk):
        """Everything to do with him talking, together in one row along the bottom."""
        row = tk.Frame(self._tk, bg=BG)
        row.pack(fill="x", padx=8, pady=(0, 8))
        left = tk.Frame(row, bg=BG)
        left.pack(side="left", fill="y", padx=(0, 8))
        self._mic_button = tk.Button(left, text="", width=13, command=self._press_mic, relief="flat",
                                     cursor="hand2", bg=PANEL, activebackground=PANEL)
        self._mic_button.pack(anchor="w")
        self._level = tk.Canvas(left, width=110, height=10, bg=PANEL, highlightthickness=0)
        self._level.pack(anchor="w", pady=(6, 0))
        self._level_bar = self._level.create_rectangle(0, 0, 0, 10, fill=ACCENT, width=0)
        tk.Button(left, text="Submit", width=13, relief="flat", cursor="hand2", command=self._submit,
                  bg="#2a4d00", fg=ACCENT, activebackground="#2a4d00",
                  activeforeground=ACCENT).pack(anchor="w", pady=(6, 0))
        self._draft = tk.Text(row, height=4, wrap="word", font=("Segoe UI", 11), bg=PANEL, fg=FG,
                              insertbackground=ACCENT, selectbackground="#3a5f00", borderwidth=0,
                              padx=8, pady=6)
        self._draft.pack(side="left", fill="both", expand=True)
        for sequence in ("<Control-Return>", "<Command-Return>"):
            self._draft.bind(sequence, self._submit_from_key)  # Ctrl+Enter sends, Enter newlines
        self._show_state(self._state)

    # ---- input, Tk thread -----------------------------------------------------------------------

    def _press_mic(self):
        """One button. While the Entity is talking it IS the stop - and stopping leaves the mic
        off, because a stop should not turn straight around and record his next breath."""
        if self._state == "speaking":
            self._on_stop()
            self._on_mic(False)
            return
        self._on_mic(self._state != "recording")

    def _submit_from_key(self, event):
        self._submit()
        return "break"  # the newline that would otherwise be typed is not wanted

    def _submit(self):
        text = self._draft.get("1.0", "end-1c").strip()
        self._draft.delete("1.0", "end")
        if text:
            self._on_submit(text)

    def _mark_dirty(self, label):
        self._sections[label]["edited"] = self._now()

    def _autosave(self):
        """Write back a section he has stopped typing in. Debounced so a save isn't attempted on
        every keystroke, and so the pane isn't re-read out from under a sentence in progress."""
        if self._profile_path is None:
            return
        for label, section in self._sections.items():
            edited = section["edited"]
            if edited is None or self._now() - edited < self.AUTOSAVE_AFTER:
                continue
            save_section(self._profile_path, self._heading_for(label),
                         section["pane"].get("1.0", "end-1c"))
            section["edited"] = None
            self._profile_stamp = None  # re-read next slow poll, so the pane shows what landed

    def _heading_for(self, label):
        """The heading this tab reads and writes. Matched by prefix, since his own headings run on
        ("Enhancements he wants for you (roadmap, not now)")."""
        wanted = self._sections[label]["heading"]
        if self._profile_path is not None and self._profile_path.exists():
            for heading in profile_sections(self._profile_path.read_text(encoding="utf-8")):
                if heading.lower().startswith(wanted.lower()):
                    return heading
        return wanted

    # ---- rendering, Tk thread -------------------------------------------------------------------

    def _show_state(self, state):
        self._state = state
        text, colour = {
            "recording": ("● listening", ACCENT),
            "muted": ("○ mic off", DIM),
            "speaking": ("◼ stop", "#ff9b9b"),
        }[state]
        self._mic_button.configure(text=text, fg=colour, activeforeground=colour)
        if state != "recording":
            self._level.coords(self._level_bar, 0, 0, 0, 10)

    def _show_level(self, level):
        self._level.coords(self._level_bar, 0, 0, int(min(1.0, level / LEVEL_FULL) * 110), 10)

    def _append_draft(self, text):
        current = self._draft.get("1.0", "end-1c")
        if current and not current.endswith((" ", "\n")):
            text = " " + text
        self._draft.insert("end", text)
        self._draft.see("end")

    def _drain_once(self):
        level = None
        for op, payload in self._feed.drain():
            if op == "level":
                level = payload  # only the newest matters; drawing each would just flicker
            elif op == "state":
                self._show_state(payload)
            elif op == "draft":
                self._append_draft(payload)
            elif op == "submit":
                self._submit()
            else:
                self._model.apply(op, payload)
        if level is not None and self._state == "recording":
            self._show_level(level)
        if self._rendered != len(self._model.entries):
            self._render_new()
        self._polls += 1
        if self._polls % self.SLOW_POLL_EVERY == 0:
            self._refresh_agent_tabs()
            self._autosave()
            self._refresh_profile_tabs()
        if self._done is not None and self._done.is_set():
            self.destroy()

    def _render_new(self):
        """Append only what's new - a thread is written once and scrolls, so re-rendering all of it
        every poll would fight his scrollback and blink the window."""
        for entry in self._model.entries[self._rendered:]:
            self._write_entry(entry)
        self._text.see("end")
        self._rendered = len(self._model.entries)

    def _write_entry(self, entry, *, pane=None, names=None):
        pane = pane or self._text
        names = names or self._speaker_names
        role = entry["role"]
        extra = ("historical",) if entry["historical"] else ()
        if role == "status":
            pane.insert("end", entry["text"] + "\n", ("status",) + extra)
            return
        name = names.get(role, "Entity · heads-up")
        pane.insert("end", f"{name} · {entry['stamp']}\n", (f"{role}:name",) + extra)
        pane.insert("end", entry["text"] + "\n", (role,) + extra)

    def _refresh_agent_tabs(self):
        """Each agent's exchange, read back the same way his own conversation is - who said what,
        and when - rather than as a wall of log lines."""
        if self._agent_logs_dir is None:
            return
        for name in discover(self._agent_logs_dir):
            if name not in self._tails:
                pane = self._make_pane(self._agent_tabs, readonly=True, font=("Segoe UI", 10))
                self._agent_tabs.add(pane, text=name)
                self._build_message_tags(pane)
                self._agent_models[name] = TranscriptModel(clock=self._clock)
                self._tails[name] = [LogTail(self._agent_logs_dir / f"{name}.log"), pane, 0]
        for name, entry in self._tails.items():
            tail, pane, rendered = entry
            new = tail.poll()
            model = self._agent_models[name]
            for line in new.splitlines():
                model.apply("history", line)
            for message in model.entries[rendered:]:
                # In an agent's tab the Entity is the one asking and the agent answers.
                self._write_entry(message, pane=pane, names={"you": "Entity", "entity": name})
            if len(model.entries) != rendered:
                pane.see("end")
            entry[2] = len(model.entries)

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
        for label, section in self._sections.items():
            if section["edited"] is not None:
                continue  # he's mid-edit; never overwrite what he's typing
            wanted = section["heading"].lower()
            body = next((text for heading, text in sections.items()
                         if heading.lower().startswith(wanted)), "")
            if body != section["pane"].get("1.0", "end-1c").rstrip():
                section["pane"].delete("1.0", "end")
                section["pane"].insert("end", body)

    # ---- lifecycle ------------------------------------------------------------------------------

    def attach_mic(self, *, submit, set_recording):
        """Wire the draft box and the mic button to a Dictation that didn't exist when the window
        opened - the window comes up first now, so he sees it while the model is still loading."""
        self._on_submit = submit
        self._on_mic = set_recording

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
        return self._sections[label]["pane"].get("1.0", "end-1c")

    def tab_padding(self, state=None):
        from tkinter import ttk

        style = ttk.Style(self._tk)
        return style.lookup("TNotebook.Tab", "padding", state and [state])

    def set_section_text(self, label, body):
        pane = self._sections[label]["pane"]
        pane.delete("1.0", "end")
        pane.insert("end", body)
        self._mark_dirty(label)

    def agent_tab_text(self, name):
        return self._tails[name][1].get("1.0", "end-1c")

    def agent_tab_labels(self):
        return [self._agent_tabs.tab(tab_id, "text") for tab_id in self._agent_tabs.tabs()]

    def justify_of(self, role):
        return str(self._text.tag_cget(role, "justify"))

    def mic_button_text(self):
        return self._mic_button.cget("text")

    def press_mic(self):
        self._press_mic()

    def destroy(self):
        if not self.ended:  # idempotent - the poll loop and a test teardown may both get here
            self.ended = True
            self._tk.destroy()
