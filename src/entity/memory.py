"""The Entity's memory of the user.

Two layers, both under the gitignored `runtime/` dir (private):
- `profile.md`  - the hand-written standing profile (goals, projects, life context).
- `learned.md`  - facts the Entity captured itself from past conversations.

Both are folded into the brain's system prompt at startup, so it knows him without being
re-told. At the end of a session the brain is asked what new, durable facts came up; those get
appended to `learned.md`, so next time it remembers them too - the auto-capture-and-remember loop.
"""

from pathlib import Path

_RUNTIME = Path(__file__).resolve().parents[2] / "runtime"
DEFAULT_PROFILE_PATH = _RUNTIME / "profile.md"
DEFAULT_LEARNED_PATH = _RUNTIME / "learned.md"

_PREAMBLE = (
    "Here is standing context about the user's life, for your awareness only. Do NOT raise any of "
    "it unprompted, and do not turn into a therapist or life-coach about it - he has real ones. "
    "Use it only to be more useful and less clueless when he brings something up himself:"
)

CONSOLIDATION_PROMPT = (
    "Our conversation is ending. List, as short bullet points (each starting with '-'), any NEW and "
    "durable facts about the user that came up and are worth remembering in future sessions - "
    "decisions, preferences, life updates, commitments - and that you didn't already know about him. "
    "Only things that will still matter later. If there is nothing new worth saving, reply with "
    "exactly: none"
)


def load_profile(path=DEFAULT_PROFILE_PATH):
    return _read(path)


def load_learned(path=DEFAULT_LEARNED_PATH):
    return _read(path)


def _read(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def compose_persona(base_persona, profile, learned=""):
    extra = "\n\n".join(section.strip() for section in (profile, learned) if section.strip())
    if not extra:
        return base_persona
    return f"{base_persona}\n\n{_PREAMBLE}\n\n{extra}"


def parse_facts(text):
    """Pull bullet-point facts out of the brain's end-of-session reply ('none' -> nothing)."""
    if text.strip().lower().rstrip(".") == "none":
        return []
    facts = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped[:1] in "-*•":
            fact = stripped[1:].strip()
            if fact:
                facts.append(fact)
    return facts


def append_learned(facts, path=DEFAULT_LEARNED_PATH):
    if not facts:
        return
    path = Path(path)
    existing = _read(path).rstrip() or "# Learned about the user"
    body = existing + "\n" + "\n".join(f"- {fact}" for fact in facts) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
