"""The Entity's standing knowledge of the user's life.

`profile.md` (kept under the gitignored `runtime/` dir, since it holds private personal
context) is loaded once at startup and folded into the brain's system prompt, so the Entity
knows his goals, projects, and situation without being told each session. This is the
"always-loaded" slice of the memory layer; dynamic recall/consolidation comes later.
"""

from pathlib import Path

DEFAULT_PROFILE_PATH = Path(__file__).resolve().parents[2] / "runtime" / "profile.md"

_PREAMBLE = (
    "Here is standing context about the user's life, for your awareness only. Do NOT raise any of "
    "it unprompted, and do not turn into a therapist or life-coach about it - he has real ones. "
    "Use it only to be more useful and less clueless when he brings something up himself:"
)


def load_profile(path=DEFAULT_PROFILE_PATH):
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def compose_persona(base_persona, profile):
    if not profile.strip():
        return base_persona
    return f"{base_persona}\n\n{_PREAMBLE}\n\n{profile.strip()}"
