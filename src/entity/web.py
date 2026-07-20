"""The window, as a local web app - the same shape as Notecraft, which this is moving to join.

The page lives in `templates/`, its look in `static/app.css`, its behaviour in `static/window.js`;
this module is the routes between them and the conversation. Nothing here draws: it hands over
entries that already know who said them, and takes back what was typed.

Why a browser engine rather than Tk: half of what a message thread wants - a box that hugs its
words, a hover that answers, a column beside the conversation - is a line of CSS and a fight with
a text widget. The measuring that Tk needed is simply gone.
"""

from flask import Flask, render_template, request

from entity.bubbles import SIDES
from entity.gui import sessions

SPEAKERS = {"you": "You", "entity": "Entity", "heads-up": "Entity · heads-up"}


def _said(entry, label=""):
    """One entry as the page needs it: who said it, when, and whether it is a message at all.

    `label` is a session's name, which the break itself does not carry - it is worked out from
    the day above it and the first thing said inside it. The break shows exactly what the row in
    the contents shows, so the two read as the same thing rather than as a rule and some dots."""
    return {
        "role": entry["role"],
        "name": SPEAKERS.get(entry["role"], ""),
        "stamp": entry["stamp"],
        "text": entry["text"],
        "label": label,
        "historical": entry["historical"],
        "bubble": entry["role"] in SIDES,
        "side": SIDES[entry["role"]][0] if entry["role"] in SIDES else "",
    }


def create_app(model, *, on_submit, on_stop=None, on_mic=None, state=None):
    app = Flask(__name__)

    @app.get("/")
    def window():
        return render_template("window.html")

    @app.get("/messages")
    def messages():
        """What the page has not drawn yet, and where each session starts.

        `since` is how much it already holds, so a poll four times a second carries a few bytes
        rather than every session ever recorded. The contents list is small and its numbering
        shifts as the conversation grows, so that goes whole each time."""
        entries = model.entries
        since = min(request.args.get("since", 0, type=int), len(entries))
        # By position, never by value: every session break is the same dict as every other, so
        # looking one up by equality sent all of them to the first one in the thread.
        found = sessions(entries)
        named = {at: label for label, at in found}
        return {
            "entries": [_said(entry, named.get(since + offset, ""))
                        for offset, entry in enumerate(entries[since:])],
            "at": since,
            "total": len(entries),
            "sessions": [{"label": label, "at": at} for label, at in found],
            "state": (state or (lambda: "muted"))(),
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

    return app
