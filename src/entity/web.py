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


def _said(entry):
    """One entry as the page needs it: who said it, when, and whether it is a message at all."""
    return {
        "role": entry["role"],
        "name": SPEAKERS.get(entry["role"], ""),
        "stamp": entry["stamp"],
        "text": entry["text"],
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
        """The whole conversation, and where each session starts in it.

        Whole rather than a tail: it is a few hundred entries of text, the page holds what it has
        already drawn, and a poll that hands back only what is new cannot say when something older
        arrived - which is exactly what preloading every session ever recorded does."""
        entries = model.entries
        return {
            "entries": [_said(entry) for entry in entries],
            "sessions": [{"label": label, "at": entries.index(opening)}
                         for label, opening in sessions(entries)],
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
