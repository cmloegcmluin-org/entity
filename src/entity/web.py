"""The window, as a local web app - Flask behind a loopback port, shown in its own desktop window.

The pages live in `templates/`, their look in `static/app.css`, their behaviour in the scripts
beside it; this module is the routes between them and the conversation. Nothing here draws: it
hands over entries that already know who said them, and takes back what was typed.

Why a browser engine rather than Tk: half of what a message thread wants - a box that hugs its
words, a hover that answers, a column beside the conversation - is a line of CSS and a fight with
a text widget. The measuring that Tk needed is simply gone.

There is no tab strip. What were tabs are pages with a bar above them: the conversation, the
profile's four sections down one page, the persona, what has been learned, and the agents.
"""

from pathlib import Path

from flask import Flask, redirect, render_template, request

from entity.memory import (
    ENHANCEMENTS_HEADING,
    checklist_items,
    profile_sections,
    save_checklist,
    save_learned,
    save_persona_additions,
    save_translations,
    translation_pairs,
)
from entity.links import link_parts, offers, open_link
from entity.mirror import SIDES, TranscriptModel, sessions
from entity.tailing import LogTail, archive_dir, discover
from entity.vocabulary import translations_in_force

# The role keys are the transcript's own line format and never change; the NAMES are what the
# window shows, and the window shows Excephalon now.
SPEAKERS = {"you": "You", "entity": "Excephalon", "heads-up": "Excephalon · heads-up"}

# The profile's own categories - each names only the stem of its heading, because a profile
# glosses its headings however it likes ("Enhancements they want for you (roadmap, not now)").
# Context reads last ("Context should be moved below Enhancements, Goals, and Projects") and as
# plain bullets: it is standing background, not a list of things to do, so boxes and an open
# count would miscount it as work. `subtitle` is the one-line explanation under each card's
# title, which every card carries now.
SECTIONS = (
    # The three checklists' subtitles are drawn from the project's founding words - "a local,
    # voice-in/voice-out, memory-persistent partner you pair with on your life... it keeps a
    # durable memory so it doesn't lose the thread across days or months."
    ("Enhancements", "Enhancements", "checklist",
     "The construction roadmap for this partner itself - file an ask and an agent can be put "
     "on it; done items keep their record in the fold below."),
    ("Goals", "Goals", "checklist",
     "The life-goal work you're pairing on - ticked when reached."),
    ("Projects", "Projects", "checklist",
     "Things to accomplish."),
    ("Context", "Life context", "bullets",
     "Background it should always hold about your life - facts, not tasks."),
)


def _said(entry, label="", speakers=SPEAKERS):
    """One entry as the page needs it: who said it, when, and whether it is a message at all.

    `label` is a session's name, which the break itself does not carry - it is worked out from
    the day above it and the first thing said inside it. The break shows exactly what the row in
    the contents shows, so the two read as the same thing rather than as a rule and some dots."""
    return {
        "role": entry["role"],
        "name": speakers.get(entry["role"], ""),
        "stamp": entry["stamp"],
        "text": entry["text"],
        "label": label,
        "historical": entry["historical"],
        "bubble": entry["role"] in SIDES,
        "side": SIDES.get(entry["role"], ""),
        # What in it can be opened, worked out here so the page only draws it. Space-aware, so a
        # path with a folder like "Field Notes" in it is one link, not one broken one.
        "parts": link_parts(entry["text"]) if entry["role"] in SIDES else [],
    }


def _thread(entries, since, speakers=SPEAKERS):
    """A stretch of conversation as the page draws it, from `since` on."""
    found = sessions(entries)
    # By position, never by value: every session break is the same dict as every other, so
    # looking one up by equality sent all of them to the first one in the thread.
    named = {at: label for label, at in found}
    return {
        "entries": [_said(entry, named.get(since + offset, ""), speakers)
                    for offset, entry in enumerate(entries[since:])],
        "at": since,
        "total": len(entries),
        "sessions": [{"label": label, "at": at} for label, at in found],
    }


def _heading(found, stem):
    """Which of the profile's own headings this section means. Matched on the stem, because a
    profile glosses its headings however it likes ("Enhancements they want for you (roadmap, not
    now)")."""
    return next((head for head in found if head.lower().startswith(stem.lower())), None)


class Agents:
    """Every agent's log, read back as the exchange it is rather than as lines.

    An agent's thread is the same shape as the conversation, with the speakers swapped: the
    Entity is the one asking, and the agent answers."""

    def __init__(self, directory, clock):
        self._directory = Path(directory) if directory else None
        # The fleet's one archive, shared with the desk's own wrap-up (see tailing.archive_dir), so
        # a log closed here and one the desk retires land in the same place.
        self._archive = archive_dir(self._directory) if self._directory else None
        self._clock = clock
        self._read = {}  # name -> (LogTail, TranscriptModel)

    def names(self):
        return sorted(discover(self._directory)) if self._directory else []

    def archived_names(self):
        """Every retired agent's log, still readable. Closing a tab moves the log here rather
        than deleting it - but with no way back in, "archived" read as "thrown away" the day a
        stranded task's only record went to the archive unverified."""
        return sorted(discover(self._archive)) if self._archive else []

    def archived_entries(self, name):
        # Keyed apart from the live reads: a fresh agent may reuse a retired one's name, and the
        # two logs are different files telling different stories.
        key = ("archived", name)
        if key not in self._read:
            self._read[key] = (LogTail(self._archive / f"{name}.log"),
                               TranscriptModel(clock=self._clock))
        tail, model = self._read[key]
        for line in tail.poll().splitlines():
            model.apply("history", line)
        return model.entries

    def close(self, name):
        """Take an agent away and archive its log, so it stays closed. Moving the log aside is
        what makes that stick - the roster is the folder, so a log still in it comes straight
        back on the next poll."""
        self._read.pop(name, None)
        log = self._directory / f"{name}.log" if self._directory else None
        if log is not None and log.exists() and self._archive is not None:
            self._archive.mkdir(parents=True, exist_ok=True)
            log.replace(self._archive / log.name)

    def entries(self, name):
        if name not in self._read:
            self._read[name] = (LogTail(self._directory / f"{name}.log"),
                                TranscriptModel(clock=self._clock))
        tail, model = self._read[name]
        for line in tail.poll().splitlines():
            model.apply("history", line)
        return model.entries


def _windows_clipboard():
    """The machine's clipboard, read by the app itself. The embedded browser gives the draft box
    no paste menu of its own, and reading the clipboard IN the page needs a browser permission
    nobody is there to grant - while this server already runs on the same machine as the text."""
    from entity.worktrees import run_hidden

    done = run_hidden(["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                      capture_output=True, text=True)
    return done.stdout.rstrip("\r\n") if done.returncode == 0 else ""


def create_app(model, *, on_submit, on_stop=None, on_mic=None, on_auto_listen=None,
               opener=open_link, mirror=None, clipboard=_windows_clipboard,
               profile_path=None, learned_path=None, translations_path=None, terms=(),
               persona_additions_path=None, agent_logs_dir=None, clock=None,
               on_quit=None, on_restart=None, upgrade_ready=None, on_translations_saved=None,
               scanned_terms=(), lexicon_reader=None, on_lexicon_saved=None):
    """`model` is the conversation to show. `mirror` is what fills it from the feed, when there
    is a live session behind it - without one the model is whatever was put in it.

    `terms` is the vocabulary transcription is biased toward, as it stood when the session opened -
    shown, not used, so they can see what it is snapping their words to. `on_quit` and
    `on_restart` drive the window this app is shown in: the page's own close dialog and the
    Restart button land here."""
    app = Flask(__name__)
    profile_path = Path(profile_path) if profile_path else None
    learned_path = Path(learned_path) if learned_path else None
    translations_path = Path(translations_path) if translations_path else None
    persona_additions_path = Path(persona_additions_path) if persona_additions_path else None
    agents = Agents(agent_logs_dir, clock)

    def _profile_text():
        if profile_path is None or not profile_path.exists():
            return {}
        return profile_sections(profile_path.read_text(encoding="utf-8"))

    @app.get("/")
    def window():
        return render_template("window.html", here="/")

    @app.get("/messages")
    def messages():
        """What the page has not drawn yet, and where each session starts.

        `since` is how much it already holds, so a poll four times a second carries a few bytes
        rather than every session ever recorded. The contents list is small and its numbering
        shifts as the conversation grows, so that goes whole each time."""
        if mirror is not None:
            mirror.drain()  # the page's poll is the pump; nothing runs while nothing is looking
        entries = model.entries
        since = min(request.args.get("since", 0, type=int), len(entries))
        retract, typed, send = mirror.dictated() if mirror is not None else (0, [], False)
        return _thread(entries, since) | {
            "state": mirror.state if mirror is not None else "waking",
            "level": mirror.level if mirror is not None else 0.0,
            "hearing": mirror.hearing if mirror is not None else "",
            "retract": retract,
            "dictated": typed,
            "send": send,
        }

    @app.post("/submit")
    def submit():
        on_submit(request.form["text"])
        return ("", 204)

    @app.post("/mic")
    def mic():
        if on_mic is not None:
            on_mic(request.form["recording"] == "true")
        return ("", 204)

    @app.post("/stop")
    def stop():
        if on_stop is not None:
            on_stop()
        return ("", 204)

    @app.post("/auto-listen")
    def auto_listen():
        """Whether the mic re-arms itself after each reply, rather than being pressed each time."""
        if on_auto_listen is not None:
            on_auto_listen(request.form["on"] == "true")
        return ("", 204)

    @app.get("/clipboard")
    def clipboard_text():
        """What the clipboard holds, for the draft's own right-click Paste (see window.js)."""
        return {"text": clipboard() if clipboard is not None else ""}

    @app.post("/open")
    def open_what_was_clicked():
        """Open something a message named - an address in the browser, a path on this machine.

        Only what this same server offered as a link: the page can ask for anything, and a POST
        that opens whatever string it is handed is a way to run things by talking to the port."""
        target = request.form["target"]
        if not offers(target):  # the same rule that offered it, so a spaced path still opens
            return ("", 400)
        opener(target)
        return ("", 204)

    # ---- the one page that was five tabs ------------------------------------------------------

    @app.get("/config")
    def config():
        """Everything he tunes, down one page with a contents column beside it: the profile's four
        checklists, what Entity has learned, its standing instructions, the words it swaps, the
        vocabulary it snaps to, and the credit line it warns from. These were five tabs
        ("'Profile' doesn't make sense as a title to me. please change it to 'Config', and
        consolidate in all the contents"), and the composed-persona dump is deliberately not
        carried over - it duplicated, worse, what this page already shows.

        Each checklist is split into what is still open and what is done: the done ones fold into
        a collapsible section at its foot, so what he still has to act on is what he sees."""
        found = _profile_text()

        def section(title, heading, kind, subtitle):
            items = checklist_items(found[heading])
            return {"title": title, "heading": heading, "kind": kind, "subtitle": subtitle,
                    "active": [item for item in items if not item["done"]],
                    "done": [item for item in items if item["done"]]}

        sections = [section(title, heading, kind, subtitle)
                    for title, heading, kind, subtitle in
                    ((title, _heading(found, stem), kind, subtitle)
                     for title, stem, kind, subtitle in SECTIONS)
                    if heading is not None]
        own = translation_pairs(_own_translations())
        stock = translations_in_force({})
        in_force = translations_in_force(own)
        learned = learned_path.read_text(encoding="utf-8") if (
            learned_path is not None and learned_path.exists()) else ""
        # The file's own "# Learned..." heading is bookkeeping, not a memory - and shown, it read
        # as one. The rows are the memories; the heading is re-derived on save.
        memories = [line.lstrip("-* ").strip() for line in learned.splitlines()
                    if line.strip() and not line.lstrip().startswith("#")]
        lexicon_now = sorted(set(lexicon_reader() if lexicon_reader is not None else ()),
                             key=str.lower)
        return render_template(
            "config.html", here="/config", sections=sections,
            # Each row remembers what the stock rule for its "heard" says, so a save can tell an
            # override worth writing from a built-in left exactly as it was - without the page
            # ever LABELLING which is which ("it doesn't matter whether a translation is
            # 'built-in' or not; don't display that").
            # One list, sorted by the RIGHT side: "for a given destination word, all the
            # existing translations into it" at a glance - nobody rallies around a mishearing.
            # The (circasonant) rows are the old Vocabulary card: the live lexicon plus what
            # the project folders contribute.
            swaps=sorted(
                [{"heard": heard, "said": in_force[heard], "stock": stock.get(heard, "")}
                 for heard in in_force]
                + [{"heard": "(circasonant)", "said": term, "stock": ""}
                   for term in set(scanned_terms) | set(lexicon_now)],
                key=lambda swap: (swap["said"].casefold(), swap["heard"].casefold())),
            memories=memories,
            instructions=[line.lstrip("-* ").strip() for line in _persona_additions().splitlines()
                          if line.strip()],
        )

    # The tabs this page replaced still answer, so a window standing open across the update lands
    # here rather than on a 404.
    for old in ("/profile", "/persona", "/memory", "/translations"):
        app.add_url_rule(old, f"was_{old.strip('/')}", lambda: redirect("/config"))

    @app.post("/profile")
    def write_profile():
        """Save one section's list back, as the markdown the file keeps and the brain reads -
        never as the boxes drawn from it.

        `drawn` is what the page believes the file holds, so an enhancement Entity filed into the
        same section while the window sat open is carried over rather than overwritten by the next
        character they type."""
        if profile_path is not None:
            sent = request.get_json()
            # Only the enhancements list carries ids - the one he refers to by number - so only it
            # numbers a new row on the way in. The other panes stay the plain lists they were.
            numbered = sent["heading"].lower().startswith(ENHANCEMENTS_HEADING.lower())
            # A bullets section (Life context) writes plain bullets back: it is background, not
            # work, and boxes in the file would draw boxes on the page again.
            plain = any(sent["heading"].lower().startswith(stem.lower())
                        for _, stem, kind, _ in SECTIONS if kind == "bullets")
            save_checklist(profile_path, sent["heading"], sent["items"], drawn=sent["drawn"],
                           number=numbered, boxes=not plain)
            # The numbering mutates the sent items in place, so this is each row's id in the order
            # the page sent them - what lets a new row show its number the moment it first saves.
            return {"ids": [item.get("id") for item in sent["items"]]}
        return ("", 204)

    def _persona_additions():
        if persona_additions_path is None or not persona_additions_path.exists():
            return ""
        return persona_additions_path.read_text(encoding="utf-8")

    @app.post("/persona")
    def write_persona():
        if persona_additions_path is not None:
            save_persona_additions(request.form["body"], persona_additions_path)
        return ("", 204)

    def _own_translations():
        if translations_path is None or not translations_path.exists():
            return ""
        return translations_path.read_text(encoding="utf-8")

    @app.post("/translations")
    def write_translations():
        if translations_path is not None:
            save_translations(request.form["body"], translations_path)
            if on_translations_saved is not None:
                # In force NOW: the running ear swaps to the saved rules for the very next chunk.
                on_translations_saved(translation_pairs(request.form["body"]))
        return ("", 204)

    @app.post("/lexicon")
    def write_lexicon():
        """The (circasonant) rows written back: additions join his lexicon, removals leave it, and
        the folder-scanned terms pass through untouched - a folder's name is not this page's to
        delete. In force immediately, like every other row on the card."""
        if on_lexicon_saved is not None:
            kept = [line.strip() for line in request.form["terms"].splitlines() if line.strip()]
            on_lexicon_saved(kept)
        return ("", 204)

    @app.post("/memory")
    def write_memory():
        if learned_path is not None:
            save_learned(request.form["body"], learned_path)
        return ("", 204)

    # ---- the window itself --------------------------------------------------------------------

    @app.post("/quit")
    def quit_window():
        """The page's own close dialog said Close - the one way the window actually goes."""
        if on_quit is not None:
            on_quit()
        return ("", 204)

    @app.get("/upgrade")
    def upgrade():
        """Whether a restart has anything to restart INTO: the checkout on disk has moved past
        the commit this process booted from. The bar's Restart button shows only then."""
        return {"ready": bool(upgrade_ready()) if upgrade_ready is not None else False}

    @app.post("/restart")
    def restart():
        """The Restart button: wind this process down and bring up a fresh one on the current
        code - how a landed fix reaches the app without him hunting down the .bat."""
        if on_restart is not None:
            on_restart()
        return ("", 204)

    @app.get("/agents")
    def show_agents():
        return render_template("agents.html", here="/agents", names=agents.names(),
                               archived=agents.archived_names())

    @app.get("/agents/archived/<name>")
    def archived_agent_thread(name):
        # The static /archived/ segment outranks the live route's converter, so an archived log
        # is reachable even while a live agent holds the same name.
        if name not in agents.archived_names():  # never read a path that did not come from the archive
            return ({"entries": [], "at": 0, "total": 0, "sessions": []}, 404)
        return _thread(agents.archived_entries(name), request.args.get("since", 0, type=int),
                       {"you": "Excephalon", "entity": name, "heads-up": "Excephalon · heads-up"})

    @app.get("/agents/<name>")
    def agent_thread(name):
        if name not in agents.names():  # never read a path that did not come from the log folder
            return ({"entries": [], "at": 0, "total": 0, "sessions": []}, 404)
        # In an agent's thread the Entity is the one asking and the agent answers.
        return _thread(agents.entries(name), request.args.get("since", 0, type=int),
                       {"you": "Excephalon", "entity": name, "heads-up": "Excephalon · heads-up"})

    @app.post("/agents/<name>/close")
    def close_agent(name):
        if name not in agents.names():  # never touch a path that did not come from the log folder
            return ("", 404)
        agents.close(name)
        return ("", 204)

    return app
