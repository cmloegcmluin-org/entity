"""A window for the Entity, so a session doesn't have to live in a terminal.

The window is a mirror, not a second implementation: the Console reports each line and WHO said it,
the Dictation reports draft text, mic state and levels, and everything flows through one thread-safe
feed into the Tk thread. What can be wrong - the message model, the feed, section parsing, log
tailing - lives outside Tk and is tested without a display; the tkinter layer mirrors state and
forwards buttons.

Layout: the conversation reads like a text thread - the user's messages in tinted boxes down the
right, Entity's down the left, each with a name and a time, past sessions above a divider. The
boxes themselves are `bubbles.py`. Everything to do with talking sits together along the bottom:
mic button, level meter, the editable draft speech types into, and Submit. Beside the conversation
are tabs - one per agent log, tailed live, and the profile's Goals, Projects and Enhancements,
editable and saved straight back into the file the brain reads.

Threads: the conversation loop and the dictation pump run on workers and push into the feed;
tkinter runs the main thread and polls with `after`, so no Tk call ever happens off the Tk thread.
"""

import queue
import time
from pathlib import Path

from entity.bubbles import NAME_FONT, SIDES, Thread
from entity.memory import find_heading, profile_sections, save_learned, save_section
from entity.tailing import LogTail, discover
from entity.theme import ACCENT, BG, DIM, FG, PANEL, SELECTION
from entity.transcript import parse_line

LEVEL_FULL = 0.06  # the meter tops out around loud speech, so ordinary talk visibly moves it

# Windows groups taskbar buttons by AppUserModelID, and a process that declares none inherits the
# identity of whatever other python-hosted app already owns a button - the Entity window turned up
# under an unrelated app's icon, wearing that app's icon. Declaring one gives it its own.
APP_ID = "Entity.VoiceCompanion"


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


def sessions(entries):
    """Each recorded session in the thread, as (label, the entry it opens with).

    What the contents list offers, and what clicking one scrolls to. A session break carries no
    date of its own: the day is the last day break above it, and the time is the first thing said
    inside it, since a session with nothing said in it is not somewhere to be sent."""
    found, day = [], ""
    opening = entries[0] if entries else None
    for entry in entries:
        role = entry["role"]
        if role == "day":
            day = entry["stamp"]
        elif role == "session":
            opening = entry
        elif role in SIDES and opening is not None:
            found.append((f"{day} {entry['stamp'][:5]}".strip(), opening))
            opening = None
    return found


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
    # The profile's own categories, in its own numbering. Slot 1 was "Entity construction", which
    # is sufficiently done, so the conversation itself takes that number. Each tab names only the
    # stem of its heading, because a profile glosses its headings however it likes.
    SECTION_TABS = (
        ("2 · Enhancements", "Enhancements"),
        ("3 · Context", "Life context"),
        ("4 · Goals", "Goals"),
        ("5 · Projects", "Projects"),
    )
    CONVERSATION_TAB = "1 · Conversation"
    PERSONA_TAB = "6 · Persona"
    MEMORY_TAB = "7 · Memory"

    def __init__(self, feed, *, on_stop, on_close, on_submit=None, on_mic=None,
                 profile_path=None, agent_logs_dir=None, persona=None, learned_path=None,
                 icon=None, title="Entity", clock=_clock, now=time.monotonic, chord=None):
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
        self._state = "muted"  # the mic starts off; nothing is heard until it is turned on
        self._now = now
        self._profile_path = Path(profile_path) if profile_path else None
        self._learned_path = Path(learned_path) if learned_path else None
        self._learned_stamp = None
        self._learned_edited = None
        self._profile_stamp = None
        self._agent_logs_dir = Path(agent_logs_dir) if agent_logs_dir else None
        self._tails = {}  # agent name -> [LogTail, Thread, TranscriptModel, rendered-entry count]
        self._sections = {}  # tab label -> {"pane": Text, "heading": str, "edited": float|None}
        self._polls = 0
        self._clock = clock
        self._speaker_names = {"you": "You", "entity": "Entity"}
        # The modifier beside the spacebar + Enter can only be heard by a keyboard hook on this
        # machine (see entity.chord); it never reaches Tk, so the window is handed a listener
        # rather than binding a key sequence for it.
        self._chord = chord

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

        self._build_menu(tk)  # before any pane, since every pane posts this one menu
        self._style_tabs(ttk)
        self._tabs = ttk.Notebook(self._tk)
        self._tabs.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        self._thread = self._make_thread(self._tabs, self._speaker_names, font=("Segoe UI", 11))
        self._tabs.add(self._thread.pane, text=self.CONVERSATION_TAB)
        for label, heading in self.SECTION_TABS:
            self._tabs.add(self._make_section_tab(tk, label, heading), text=label)
        # Everything it has been told about how to be - the standing rules, and every one added
        # since, in the words it actually reads. Read-only: this is assembled at startup, not typed.
        self._persona = self._make_pane(self._tabs, readonly=True, font=("Segoe UI", 10))
        self._tabs.add(self._persona, text=self.PERSONA_TAB)
        if persona:
            self._persona.insert("end", persona)
        # What it has learned about the user, as it learns it - the one place a self-improvement
        # it is told about actually lands, so it can be read the moment it lands and crossed out.
        self._learned = self._make_pane(self._tabs, readonly=False, font=("Segoe UI", 10))
        self._learned.bind("<KeyRelease>", lambda event: setattr(self, "_learned_edited", self._now()))
        self._tabs.add(self._learned, text=self.MEMORY_TAB)
        # One home for the agents, however many end up being driven at once.
        self._agent_tabs = ttk.Notebook(self._tabs)
        self._agent_tabs.bind("<Button-3>", self._clicked_agent_tabs)
        self._tabs.add(self._agent_tabs, text="Agents")
        self._build_controls(tk)
        self._tk.protocol("WM_DELETE_WINDOW", on_close)
        if self._chord is not None:
            self._chord.start()

    # ---- construction ---------------------------------------------------------------------------

    def _style_tabs(self, ttk):
        style = ttk.Style(self._tk)
        style.theme_use("clam")
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL, foreground=DIM, padding=(12, 6))
        # Told apart by its lighter panel and plain bright text - no color, and no resizing: clam
        # grows/shrinks the selected tab by default, which left the active one a different height
        # from its neighbours.
        style.map("TNotebook.Tab", background=[("selected", "#333333")], foreground=[("selected", FG)],
                  expand=[("selected", [0, 0, 0, 0])], padding=[("selected", (12, 6))])

    def _make_pane(self, parent, *, readonly, font=("Consolas", 11)):
        """A pane that can always be selected and copied from. Read-only panes stay in the
        NORMAL state and refuse edits key by key instead - a disabled Text widget takes no
        focus, so Ctrl-C never reaches it and the conversation cannot be got out of the
        window."""
        from tkinter import scrolledtext

        pane = scrolledtext.ScrolledText(
            parent, wrap="word", font=font, bg=PANEL, fg=FG, selectbackground=SELECTION,
            borderwidth=0, padx=10, pady=8, insertbackground=ACCENT,
        )
        if readonly:
            self._make_readonly(pane)
        return pane

    def _make_thread(self, parent, names, *, font):
        """A pane that reads as a message thread: bubbles, not lines."""
        pane = self._make_pane(parent, readonly=True, font=font)
        return Thread(pane, names, prepare=self._make_readonly)

    def _make_readonly(self, widget):
        """No caret and no typing, but every bit of selecting and copying still works."""
        widget.bind("<Key>", self._refuse_edit)
        widget.configure(insertwidth=0)  # reads as text rather than as a box to type in
        self._make_copyable(widget)

    def _build_menu(self, tk):
        """ONE right-click menu, for the window rather than per widget. A menu of its own on every
        bubble ran Tk out of menu handles - "No more menus can be allocated" - once the scrollback
        went back far enough, and the window then would not open at all. Which widget was clicked
        is remembered as the menu is posted instead."""
        self._clicked = None  # the widget the menu was posted over
        self._menu = tk.Menu(self._tk, tearoff=0, bg=PANEL, fg=FG, activebackground=SELECTION)
        self._menu.add_command(label="Copy", command=lambda: self._copy_selection(self._clicked))

    def _make_copyable(self, widget):
        """Selecting and copying has to WORK, not merely be permitted - it did not. A click
        takes focus (so the keystroke has somewhere to land), Ctrl-C and Ctrl-A are bound
        outright rather than left to the class bindings, and a right-click offers Copy for when
        hands aren't on the keyboard."""
        widget.bind("<Button-1>", lambda event: widget.focus_set(), add="+")
        widget.tag_raise("sel")  # or a tag made later wins and the highlight is invisible
        widget.bind("<Control-c>", lambda event: self._copy_selection(widget))
        widget.bind("<Control-C>", lambda event: self._copy_selection(widget))
        widget.bind("<Control-a>", lambda event: self._select_all(widget))
        widget.bind("<Control-A>", lambda event: self._select_all(widget))
        widget.bind("<Button-3>", lambda event: self._popup(widget, event))

    def _popup(self, widget, event):
        self._clicked = widget
        self._menu.tk_popup(event.x_root, event.y_root)

    def _copy_selection(self, widget):
        try:
            selected = widget.get("sel.first", "sel.last")
        except Exception:
            return "break"  # nothing selected; copying nothing would only clear the clipboard
        self._to_clipboard(selected)
        return "break"

    def _to_clipboard(self, text):
        if not text:
            return
        self._tk.clipboard_clear()
        self._tk.clipboard_append(text)

    @staticmethod
    def _select_all(widget):
        widget.tag_add("sel", "1.0", "end-1c")
        return "break"

    @staticmethod
    def _refuse_edit(event):
        """Swallow anything that would type into a read-only pane, while leaving selection,
        scrolling and copying alone."""
        allowed = {"Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next"}
        if event.keysym in allowed or event.state & 0x4:  # 0x4 is Control: copy, select-all
            return None
        return "break"

    def _make_section_tab(self, tk, label, heading):
        """The user's own documents, edited in place - no Save button, because a document you have to
        remember to save is one you lose."""
        pane = self._make_pane(self._tabs, readonly=False)
        pane.bind("<KeyRelease>", lambda event, name=label: self._mark_dirty(name))
        self._sections[label] = {"pane": pane, "heading": heading, "edited": None, "loaded": ""}
        return pane

    def _build_controls(self, tk):
        """Everything to do with talking, together in one row along the bottom, with the contents
        of the conversation above it in the same column."""
        row = tk.Frame(self._tk, bg=BG)
        row.pack(fill="x", padx=8, pady=(0, 8))
        left = tk.Frame(row, bg=BG)
        left.pack(side="left", fill="y", padx=(0, 8))
        # Every session, oldest first, as somewhere to jump to. Its width is the column's, which
        # the buttons below size; its height is a few rows, and it scrolls past that.
        self._contents = tk.Listbox(left, height=4, width=13, font=NAME_FONT, bg=PANEL, fg=DIM,
                                    selectbackground=SELECTION, selectforeground=FG,
                                    borderwidth=0, highlightthickness=0, activestyle="none",
                                    cursor="hand2", exportselection=False)
        self._contents.pack(fill="both", expand=True, pady=(0, 6))
        self._contents.bind("<<ListboxSelect>>", lambda event: self._go_to_session())
        self._listed = []  # the entries the rows point at, in the same order
        self._mic_button = tk.Button(left, text="", width=13, command=self._press_mic, relief="flat",
                                     cursor="hand2", bg=PANEL, activebackground=PANEL)
        self._mic_button.pack(fill="x")
        # The width comes from the column, which the buttons above and below size in characters:
        # fixed at 110px the meter stopped visibly short of both of them. It still has to ask for
        # something, and asking for nothing means asking for a canvas's default 378px, which then
        # sets the column's width itself and takes a third of the window off the draft box.
        self._level = tk.Canvas(left, width=1, height=10, bg=PANEL, highlightthickness=0)
        self._level.pack(fill="x", pady=(6, 0))
        self._level_bar = self._level.create_rectangle(0, 0, 0, 10, fill=ACCENT, width=0)
        self._submit = tk.Button(left, text="Submit", width=13, relief="flat", cursor="hand2",
                                 command=self._submit_draft, bg="#2a4d00", fg=ACCENT,
                                 activebackground="#2a4d00", activeforeground=ACCENT)
        self._submit.pack(fill="x", pady=(6, 0))
        self._draft = tk.Text(row, height=4, wrap="word", font=("Segoe UI", 11), bg=PANEL, fg=FG,
                              insertbackground=ACCENT, selectbackground="#3a5f00", borderwidth=0,
                              padx=8, pady=6)
        self._draft.pack(side="left", fill="both", expand=True)
        self._draft.bind("<Key-Return>", self._submit_from_key)
        self._show_state(self._state)

    # ---- input, Tk thread -----------------------------------------------------------------------

    def _press_mic(self):
        """One button. While the Entity is talking it IS the stop - and stopping leaves the mic
        off, because a stop should not turn straight around and record the next breath."""
        if self._state == "speaking":
            self._on_stop()
            self._on_mic(False)
            return
        self._on_mic(self._state != "recording")

    # The modifier bits Tk on Windows sets for Ctrl and Alt, measured rather than assumed. The
    # Windows key sets none of them - it never reaches Tk here at all - so that chord is the
    # keyboard hook's job (entity.chord), not this binding's.
    MODIFIERS = 0x4 | 0x20000

    def _submit_from_key(self, event):
        if not event.state & self.MODIFIERS:
            return None  # a bare Enter is a new line in the draft, as it should be
        self._submit_draft()
        return "break"  # and the newline that would otherwise be typed is not wanted

    def _submit_draft(self):
        text = self._draft.get("1.0", "end-1c").strip()
        self._draft.delete("1.0", "end")
        if text:
            self._on_submit(text)

    def _mark_dirty(self, label):
        self._sections[label]["edited"] = self._now()

    def _autosave(self):
        """Write back a section that is no longer being typed in. Debounced so a save isn't attempted on
        every keystroke, and so the pane isn't re-read out from under a sentence in progress."""
        if self._profile_path is None:
            return
        if (self._learned_path is not None and self._learned_edited is not None
                and self._now() - self._learned_edited >= self.AUTOSAVE_AFTER):
            save_learned(self._learned.get("1.0", "end-1c"), self._learned_path)
            self._learned_edited = None
            self._learned_stamp = None
        for label, section in self._sections.items():
            edited = section["edited"]
            if edited is None or self._now() - edited < self.AUTOSAVE_AFTER:
                continue
            save_section(self._profile_path, self._heading_for(label),
                         section["pane"].get("1.0", "end-1c"), keeping=section["loaded"])
            section["edited"] = None
            self._profile_stamp = None  # re-read next slow poll, so the pane shows what landed

    def _heading_for(self, label):
        """The profile's own heading that this tab reads and writes - see `find_heading`."""
        wanted = self._sections[label]["heading"]
        if self._profile_path is None or not self._profile_path.exists():
            return wanted
        return find_heading(profile_sections(self._profile_path.read_text(encoding="utf-8")), wanted)

    # ---- rendering, Tk thread -------------------------------------------------------------------

    def _show_state(self, state):
        self._state = state
        text, color = {
            "recording": ("● listening", ACCENT),
            "muted": ("○ mic off", DIM),
            "speaking": ("◼ stop", "#ff9b9b"),
        }[state]
        self._mic_button.configure(text=text, fg=color, activeforeground=color)
        if state != "recording":
            self._level.coords(self._level_bar, 0, 0, 0, 10)

    def _show_level(self, level):
        span = self._level.winfo_width()  # the column's width, not a number fixed at build time
        self._level.coords(self._level_bar, 0, 0, int(min(1.0, level / LEVEL_FULL) * span), 10)

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
                self._submit_draft()
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
            self._refresh_learned()
        if self._done is not None and self._done.is_set():
            self.destroy()

    def _render_new(self):
        """Append only what's new - a thread is written once and scrolls, so re-rendering all of it
        every poll would fight the scrollback and blink the window."""
        self._thread.show(self._model.entries[self._rendered:])
        self._thread.pane.see("end")
        self._rendered = len(self._model.entries)
        self._list_sessions()

    def _list_sessions(self):
        """Re-fill the contents, which only ever gains rows as the conversation goes on."""
        listed = sessions(self._model.entries)
        if [label for label, _ in listed] == [label for label, _ in self._listed]:
            return
        self._listed = listed
        self._contents.delete(0, "end")
        for label, _ in listed:
            self._contents.insert("end", label)

    def _go_to_session(self):
        """A row was clicked: scroll to the session it names, however far back it is held."""
        chosen = self._contents.curselection()
        if chosen:
            self._thread.reveal(self._listed[chosen[0]][1])

    def _refresh_agent_tabs(self):
        """Each agent's exchange, read back the same way the conversation is - who said what,
        and when - rather than as a wall of log lines."""
        if self._agent_logs_dir is None:
            return
        for name in list(self._tails):
            if name not in discover(self._agent_logs_dir):
                self.close_agent_tab(name)  # its log is gone, so nobody wants the tab
        for name in discover(self._agent_logs_dir):
            if name not in self._tails:
                # In an agent's tab the Entity is the one asking and the agent answers.
                thread = self._make_thread(self._agent_tabs, {"you": "Entity", "entity": name},
                                           font=("Segoe UI", 10))
                self._agent_tabs.add(thread.pane, text=f"{name}  ✕")
                self._tails[name] = [LogTail(self._agent_logs_dir / f"{name}.log"), thread,
                                     TranscriptModel(clock=self._clock), 0]
        for entry in self._tails.values():
            tail, thread, model, rendered = entry
            for line in tail.poll().splitlines():
                model.apply("history", line)
            thread.show(model.entries[rendered:])
            if len(model.entries) != rendered:
                thread.pane.see("end")
            entry[3] = len(model.entries)

    def _refresh_learned(self):
        """Re-read what it has learned, unless it is in the middle of being edited."""
        if self._learned_path is None or self._learned_edited is not None:
            return
        try:
            stamp = self._learned_path.stat().st_mtime
        except OSError:
            return
        if stamp == self._learned_stamp:
            return
        self._learned_stamp = stamp
        text = self._learned_path.read_text(encoding="utf-8")
        if text.rstrip() != self._learned.get("1.0", "end-1c").rstrip():
            self._learned.delete("1.0", "end")
            self._learned.insert("end", text)

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
                continue  # mid-edit; never overwrite what is being typed
            body = sections.get(find_heading(sections, section["heading"]), "")
            if body != section["pane"].get("1.0", "end-1c").rstrip():
                section["pane"].delete("1.0", "end")
                section["pane"].insert("end", body)
            section["loaded"] = body  # what the edit started from, so a save can tell it from Entity's

    # ---- lifecycle ------------------------------------------------------------------------------

    def attach_mic(self, *, submit, set_recording):
        """Wire the draft box and the mic button to a Dictation that didn't exist when the window
        opened - the window comes up first now, so it is visible while the model is still loading."""
        self._on_submit = submit
        self._on_mic = set_recording

    def close_agent_tab(self, name):
        """Take an agent's tab away and archive its log, so it stays closed. Asked how to close
        these; Entity can close one the same way, by moving the log aside."""
        entry = self._tails.pop(name, None)
        if entry is not None:
            self._agent_tabs.forget(entry[1].pane)
        if self._agent_logs_dir is not None:
            log = self._agent_logs_dir / f"{name}.log"
            if log.exists():
                closed = self._agent_logs_dir / "closed"
                closed.mkdir(parents=True, exist_ok=True)
                log.replace(closed / log.name)

    def _clicked_agent_tabs(self, event):
        """A click on a tab's ✕ closes it."""
        try:
            index = self._agent_tabs.index(f"@{event.x},{event.y}")
        except Exception:
            return
        label = self._agent_tabs.tab(index, "text")
        if label.endswith("✕") and event.x > self._agent_tabs.winfo_x():
            name = label.rsplit("  ", 1)[0]
            if name in self._tails:
                self.close_agent_tab(name)

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

    def hide(self):
        """Lay the window out for real without showing it, so a test can measure what it built and
        still flash nothing on screen. Withdrawing is not enough: an unmapped window reports
        every width as 1, and a bubble's whole point is the width it ends up."""
        self._tk.attributes("-alpha", 0.0)
        self._tk.deiconify()
        self._tk.update()  # the map event, so the panes have their real widths from here on

    def widget_text(self):
        return self._thread.text()

    def pane_width(self):
        return self._thread.pane.winfo_width()

    def bubble_geometry(self):
        """Where each bubble actually landed: (role, x, width), measured off the real widgets."""
        self._tk.update_idletasks()  # let the layout settle, or every box still reads as 1px
        return self._thread.geometry()

    def waiting(self):
        """How much of the past is loaded but not built - what scrolling back would still add."""
        return self._thread.waiting()

    def scroll_to_top(self):
        """Drag the scrollbar to the top, as a reader would, and let what that pulls in settle."""
        self._thread.pane.yview_moveto(0.0)
        self._tk.update()

    def control_widths(self):
        """The bottom-left column as it actually laid out: mic, meter, Submit, contents."""
        self._tk.update_idletasks()
        return (self._mic_button.winfo_width(), self._level.winfo_width(),
                self._submit.winfo_width(), self._contents.winfo_width())

    def contents(self):
        """The sessions the contents list is offering."""
        return list(self._contents.get(0, "end"))

    def click_contents(self, row):
        """Click a row of the contents, as a reader would, and let what that pulls in settle."""
        self._contents.selection_clear(0, "end")
        self._contents.selection_set(row)
        self._contents.event_generate("<<ListboxSelect>>")
        self._tk.update()

    def menu_labels(self):
        """What the right-click menu offers."""
        return [self._menu.entrycget(index, "label") for index in range(self._menu.index("end") + 1)]

    def menu_count(self):
        """How many Tk menus the window holds - which must not grow with the conversation."""
        def menus(widget):
            return (widget.winfo_class() == "Menu") + sum(menus(kid)
                                                          for kid in widget.winfo_children())

        return menus(self._tk)

    def bubble_text(self, index):
        """The words actually sitting in one bubble, read off the widget."""
        return self._thread.bodies()[index].get("1.0", "end-1c")

    def hover_gap(self, index):
        """How far clear of a bubble the copy button lands when that bubble is hovered."""
        return self._thread.hover_gap(index)

    def hover_copies(self, index):
        """What pressing the copy button a hovered bubble offers puts on the clipboard."""
        return self._thread.hover_copies(index)

    def copy_from_bubble(self, index, start, end):
        """Select part of one bubble and press Ctrl-C, the way a reader would, and read the
        clipboard back.

        Emptied first: whatever was last copied in another app is on that clipboard, and reading
        it back would let a copy that never happened look exactly like one that did."""
        self._tk.clipboard_clear()
        body = self._thread.bodies()[index]
        body.focus_set()
        body.tag_add("sel", start, end)
        body.event_generate("<Control-c>")
        return self._tk.clipboard_get()

    def draft_text(self):
        return self._draft.get("1.0", "end-1c")

    def tab_labels(self):
        return [self._tabs.tab(tab_id, "text") for tab_id in self._tabs.tabs()]

    def persona_text(self):
        return self._persona.get("1.0", "end-1c")

    def memory_text(self):
        return self._learned.get("1.0", "end-1c")

    def set_memory_text(self, text):
        self._learned.delete("1.0", "end")
        self._learned.insert("end", text)
        self._learned_edited = self._now()

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
        return self._tails[name][1].text()

    def agent_tab_labels(self):
        return [self._agent_tabs.tab(tab_id, "text") for tab_id in self._agent_tabs.tabs()]

    def mic_button_text(self):
        return self._mic_button.cget("text")

    def press_mic(self):
        self._press_mic()

    def destroy(self):
        if not self.ended:  # idempotent - the poll loop and a test teardown may both get here
            self.ended = True
            if self._chord is not None:
                self._chord.stop()  # a global keyboard hook must not outlive the window it serves
            self._tk.destroy()
