"""The window, as a local web app - the same shape as Notecraft, which this is moving to join.

The pages live in `templates/`, their look in `static/app.css`, their behaviour in the scripts
beside it; this module is the routes between them and the conversation. Nothing here draws: it
hands over entries that already know who said them, and takes back what was typed.

Why a browser engine rather than Tk: half of what a message thread wants - a box that hugs its
words, a hover that answers, a column beside the conversation - is a line of CSS and a fight with
a text widget. The measuring that Tk needed is simply gone.

There is no tab strip. What were tabs are pages with a bar above them: the conversation, the
profile's four sections down one page, the persona, what has been learned, and the agents.
"""

import re
from pathlib import Path

from flask import Flask, render_template, request

from entity.memory import (
    checklist_shown,
    checklist_stored,
    profile_sections,
    save_learned,
    save_section,
    save_translations,
    translation_pairs,
)
from entity.links import link_in, open_link
from entity.mirror import SIDES, TranscriptModel, sessions
from entity.tailing import LogTail, discover
from entity.vocabulary import translations_in_force

SPEAKERS = {"you": "You", "entity": "Entity", "heads-up": "Entity · heads-up"}

# The profile's own categories, in its own numbering, minus the one the conversation itself is.
# Each names only the stem of its heading, because a profile glosses its headings however it likes
# ("Enhancements he wants for you (roadmap, not now)").
SECTIONS = (("Enhancements", "Enhancements"), ("Context", "Life context"),
            ("Goals", "Goals"), ("Projects", "Projects"))


def _parts(text):
    """A message split into what can be opened and what cannot.

    Entity names paths and addresses constantly, and reading one back to retype it is the thing
    this saves. The server decides what is a link - `links.link_in` is where the rules live and
    where they are tested - so the page only draws what it is handed."""
    parts, plain = [], []
    for word in text.split(" "):
        target = link_in(word)
        if target is None:
            plain.append(word)
            continue
        if plain:
            parts.append({"text": " ".join(plain) + " ", "link": ""})
            plain = []
        # The sentence's own punctuation stays outside the link, so a full stop after a filename
        # is not part of the filename.
        head, _, rest = word.partition(target)
        parts.append({"text": head, "link": ""}) if head else None
        parts.append({"text": target, "link": target})
        parts.append({"text": rest + " ", "link": ""})
    if plain:
        parts.append({"text": " ".join(plain), "link": ""})
    return [part for part in parts if part["text"]]


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
        # What in it can be opened, worked out here so the page only draws it.
        "parts": _parts(entry["text"]) if entry["role"] in SIDES else [],
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


# The persona marks its own sections by shouting - "BREVITY IS YOUR MOST IMPORTANT RULE",
# "SURFACE FAILURES IMMEDIATELY", "HOW TO PUT AN AGENT ON WORK". Two capitalised words in a row at
# the start of a sentence is that and nothing else, and it is the only structure six thousand
# characters arriving on one line have.
_SHOUT = r"[A-Z][A-Z'’]*(?:[ ,-]+[A-Z][A-Z'’]*)+"
_OPENS_A_SECTION = re.compile(rf"(?<=[.:!?])\s+(?={_SHOUT}\b)")
# It is a heading only when it FINISHES - on a full stop, or on the dash that introduces what it
# is about. Running straight on into lowercase ("A CORE part of your job", "ONE EXCEPTION, and it
# overrides brevity") it is emphasis inside a sentence, and pulling it out would break the sentence
# in half.
_LEAD = re.compile(rf"^({_SHOUT}(?:[.:!?]|(?=\s+[-–—]\s)))\s*")


def persona_paragraphs(text):
    """The persona as something readable, without one word of it being changed.

    This is the exact text the brain reads - the window shows it so he can see what it has been
    told, and a second, tidied copy would drift from the real one. So nothing here rewrites: it
    only decides where the breaks go. Blank lines are breaks already (his profile arrives with its
    own headings and bullets); the rest are where the persona starts shouting, which is where its
    author meant a new section.

    Each block is {lead, body} - the shouted opening, if it has one, and the rest."""
    blocks = []
    for paragraph in text.split("\n\n"):
        for part in _OPENS_A_SECTION.split(paragraph):
            part = part.strip()
            if not part:
                continue
            found = _LEAD.match(part)
            lead = found.group(1) if found else ""
            blocks.append({"lead": lead, "body": part[found.end():] if found else part})
    return blocks


def _heading(found, stem):
    """Which of the profile's own headings this section means. Matched on the stem, because a
    profile glosses its headings however it likes ("Enhancements he wants for you (roadmap, not
    now)")."""
    return next((head for head in found if head.lower().startswith(stem.lower())), None)


def _items(body):
    """A checklist's lines as things to tick: whether each is done, and what it says.

    Any line with words on it is an item - they are typed in plain, and boxing only what is
    already punctuated as a bullet left additions sitting outside the list they were meant to
    join. A blank line is the gap that was left, and stays one."""
    return [{"done": line.startswith("☑"), "text": line[1:].strip(), "blank": not line.strip()}
            for line in checklist_shown(body).splitlines()]


class Agents:
    """Every agent's log, read back as the exchange it is rather than as lines.

    An agent's thread is the same shape as the conversation, with the speakers swapped: the
    Entity is the one asking, and the agent answers."""

    def __init__(self, directory, clock):
        self._directory = Path(directory) if directory else None
        self._clock = clock
        self._read = {}  # name -> (LogTail, TranscriptModel)

    def names(self):
        return sorted(discover(self._directory)) if self._directory else []

    def close(self, name):
        """Take an agent away and archive its log, so it stays closed. Moving the log aside is
        what makes that stick - the roster is the folder, so a log still in it comes straight
        back on the next poll."""
        self._read.pop(name, None)
        log = self._directory / f"{name}.log" if self._directory else None
        if log is not None and log.exists():
            closed = self._directory / "closed"
            closed.mkdir(parents=True, exist_ok=True)
            log.replace(closed / log.name)

    def entries(self, name):
        if name not in self._read:
            self._read[name] = (LogTail(self._directory / f"{name}.log"),
                                TranscriptModel(clock=self._clock))
        tail, model = self._read[name]
        for line in tail.poll().splitlines():
            model.apply("history", line)
        return model.entries


def create_app(model, *, on_submit, on_stop=None, on_mic=None, on_auto_listen=None,
               opener=open_link, mirror=None,
               profile_path=None, learned_path=None, translations_path=None, terms=(), persona="",
               agent_logs_dir=None, clock=None):
    """`model` is the conversation to show. `mirror` is what fills it from the feed, when there
    is a live session behind it - without one the model is whatever was put in it.

    `terms` is the vocabulary transcription is biased toward, as it stood when the session opened -
    shown, not used, so he can see what it is snapping his words to."""
    app = Flask(__name__)
    profile_path = Path(profile_path) if profile_path else None
    learned_path = Path(learned_path) if learned_path else None
    translations_path = Path(translations_path) if translations_path else None
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
            "state": mirror.state if mirror is not None else "muted",
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

    @app.post("/open")
    def open_what_was_clicked():
        """Open something a message named - an address in the browser, a path on this machine.

        Only what this same server offered as a link: the page can ask for anything, and a POST
        that opens whatever string it is handed is a way to run things by talking to the port."""
        target = request.form["target"]
        if link_in(target) != target:
            return ("", 400)
        opener(target)
        return ("", 204)

    # ---- the pages that were tabs -------------------------------------------------------------

    @app.get("/profile")
    def profile():
        """The four sections, down one page, every one of them a checklist. They are the same kind
        of thing - a list of lines under a heading - and only Enhancements drew boxes, so three of
        the four handed him raw markdown to decode. Matched by prefix, since a profile glosses its
        own headings, and shown in the profile's order rather than ours where both agree."""
        found = _profile_text()
        sections = [
            # A box to click, not `- [x]` spelled out for the reader to decode.
            {"title": title, "heading": heading, "body": found[heading],
             "items": _items(found[heading])}
            for title, heading in ((title, _heading(found, stem)) for title, stem in SECTIONS)
            if heading is not None
        ]
        return render_template("profile.html", here="/profile", sections=sections)

    @app.post("/profile")
    def write_profile():
        """Save one section back, keeping what was there when the page was drawn - so a save can
        tell an edit from a change the brain made underneath it. It goes back as the markdown the
        file keeps and the brain reads, never as the boxes drawn from it."""
        if profile_path is not None:
            heading = request.form["heading"]
            save_section(profile_path, heading, checklist_stored(request.form["body"]),
                         keeping=_profile_text().get(heading, ""))
        return ("", 204)

    @app.get("/persona")
    def show_persona():
        return render_template("persona.html", here="/persona",
                               blocks=persona_paragraphs(persona), length=len(persona))

    def _his_translations():
        if translations_path is None or not translations_path.exists():
            return ""
        return translations_path.read_text(encoding="utf-8")

    @app.get("/translations")
    def translations():
        """What it is quietly rewriting, written out. "Cloud agent" for "Claude agent" is a
        correction he cannot see happening and cannot argue with, and he asked to see the lot -
        the ones that ship and the ones he adds, in one list, with the words the fuzzy pass snaps
        toward underneath them."""
        his = translation_pairs(_his_translations())
        in_force = translations_in_force(his)
        return render_template(
            "translations.html", here="/translations",
            rows=[{"heard": heard, "said": in_force[heard], "his": heard in his}
                  for heard in sorted(in_force)],
            mine=_his_translations(),
            terms=sorted(terms, key=str.lower),
        )

    @app.post("/translations")
    def write_translations():
        if translations_path is not None:
            save_translations(request.form["body"], translations_path)
        return ("", 204)

    @app.get("/memory")
    def memory():
        learned = learned_path.read_text(encoding="utf-8") if (
            learned_path is not None and learned_path.exists()) else ""
        return render_template("memory.html", here="/memory", learned=learned)

    @app.post("/memory")
    def write_memory():
        if learned_path is not None:
            save_learned(request.form["body"], learned_path)
        return ("", 204)

    @app.get("/agents")
    def show_agents():
        return render_template("agents.html", here="/agents", names=agents.names())

    @app.get("/agents/<name>")
    def agent_thread(name):
        if name not in agents.names():  # never read a path that did not come from the log folder
            return ({"entries": [], "at": 0, "total": 0, "sessions": []}, 404)
        # In an agent's thread the Entity is the one asking and the agent answers.
        return _thread(agents.entries(name), request.args.get("since", 0, type=int),
                       {"you": "Entity", "entity": name, "heads-up": "Entity · heads-up"})

    @app.post("/agents/<name>/close")
    def close_agent(name):
        if name not in agents.names():  # never touch a path that did not come from the log folder
            return ("", 404)
        agents.close(name)
        return ("", 204)

    return app
