"""Which model an agent is put on, and how hard it is told to think.

The user picks this out loud - "use Fable on max", "put it back on Opus" - so the names here are the
ones they say, not the ids the API wants. Sonnet was the hardcoded default and nobody could see it,
let alone change it: they had to ask what model their agents were running and was told, truthfully and
uselessly, that the dispatch doesn't expose one.

Effort travels with the model because they say them together, and because they are one decision: a
smaller model at max effort and a bigger one at low effort are different trades, not settings.
"""

# What they say -> what the API wants. Bare family names, since that is how they say them; an id they
# pastes in verbatim is passed through untouched (see `resolve`).
FAMILIES = {
    "opus": "claude-opus-4-8",
    "fable": "claude-fable-5",
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5-20251001",
}

# The SDK's own ladder, in order, so "on high" means what the docs mean by it.
EFFORTS = ("low", "medium", "high", "xhigh", "max")

# Their default, in their words: Opus 4.8 on High. The work these agents do is their real work - the
# thing they are trying to stop having to supervise - so it gets the model they would have used themselves.
DEFAULT_MODEL = FAMILIES["opus"]
DEFAULT_EFFORT = "high"


def resolve(spoken):
    """(model, effort) from what they said, or None if there is no model in it.

    Either half may be missing and the other still lands: "on max" keeps the model and raises the
    effort, "use Fable" keeps the effort and changes the model. Returning None for a phrase with
    neither is what lets the caller say so, rather than silently choosing something they didn't ask
    for and running their work on it.
    """
    words = [word.strip(".,;:!?").lower() for word in str(spoken).split()]
    model = next((FAMILIES[word] for word in words if word in FAMILIES), None)
    if model is None:  # a full id, pasted or spelled out, is their choice as much as a family name
        model = next((word for word in words if word.startswith("claude-")), None)
    effort = next((word for word in words if word in EFFORTS), None)
    return None if model is None and effort is None else (model, effort)


def describe(model, effort):
    """The choice as a short phrase to say back, in the words they used for it."""
    family = next((name for name, full in FAMILIES.items() if full == model), model)
    return f"{family.capitalize()} on {effort}"
