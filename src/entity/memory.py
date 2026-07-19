"""The Entity's memory of the user.

Three layers, two of them under the gitignored `runtime/` dir (private):
- `profile.md`  - the hand-written standing profile (goals, projects, life context).
- `learned.md`  - facts the Entity captured itself from past conversations.
- `lexicon.md`  - his working vocabulary: names he coined (Notecraft, WaveShaper, Skylark) AND the
                  domain terms and proper nouns of his fields (Bayesian, acoustic, a collaborator,
                  ...) - one word or several. Triple duty: it's part of the brain's standing
                  context so it knows his words, transcription here is biased toward the same
                  list (see `vocabulary`), and Notecraft corrects its voice memos against it too —
                  so teaching a term once fixes all three. That last one is why it lives outside
                  this repo, in the state folder Notecraft syncs between his machines.

All are folded into the brain's system prompt at startup, so it knows him without being
re-told. At the end of a session the brain is asked what new, durable facts came up; those get
appended to `learned.md`, so next time it remembers them too - the auto-capture-and-remember loop.
"""

import re
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parents[2] / "runtime"
DEFAULT_PROFILE_PATH = _RUNTIME / "profile.md"
DEFAULT_LEARNED_PATH = _RUNTIME / "learned.md"
# The lexicon is the one file Notecraft reads too, so a term taught in either place fixes
# both. Notecraft runs on his MacBook besides this PC, so the shared list lives in the
# state folder it syncs between them (its NOTECRAFT_STATE_DIR); a file under this PC-only
# checkout could never reach the other machine.
DEFAULT_LEXICON_PATH = Path.home() / "Notecraft" / "state" / "lexicon.md"

_PREAMBLE = (
    "Here is standing context about the user's life, for your awareness only. Do NOT raise any of "
    "it unprompted, and do not turn into a therapist or life-coach about it - he has real ones. "
    "Use it only to be more useful and less clueless when he brings something up himself:"
)

_LEXICON_INTRO = (
    "This is the user's working vocabulary - not only names he coined, but the domain terms, proper "
    "nouns and terms of art of the fields he lives in (his projects, acoustic music and notation, "
    "film, his health, the people he works with). Recognize them when he uses them, and get them "
    "right when you use them back - his speech-to-text is biased toward this same list. Don't force "
    "them into the conversation:"
)

# A gloss can follow the term after " - " / " — " / ": "; the term itself is the head of the line.
_GLOSS = re.compile(r"\s+[—–-]\s+|:\s+")

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


def load_lexicon(path=DEFAULT_LEXICON_PATH):
    return _read(path)


def _read(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def lexicon_terms(text):
    """The bare terms from a lexicon file, for biasing transcription - the head of each line (one
    word or a whole phrase), with any gloss, bullet, blank line or '#' comment stripped off."""
    terms = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line[0] in "-*•":
            line = line[1:].strip()
        term = _GLOSS.split(line, maxsplit=1)[0].strip()
        if term:
            terms.append(term)
    return terms


def compose_persona(base_persona, profile, learned="", lexicon=""):
    """Fold his standing context into the brain's system prompt: life context (profile + learned)
    under a do-not-play-therapist warning, and his lexicon under its own recognize-these framing."""
    life = "\n\n".join(section.strip() for section in (profile, learned) if section.strip())
    sections = [base_persona]
    if life:
        sections.append(f"{_PREAMBLE}\n\n{life}")
    if lexicon.strip():
        sections.append(f"{_LEXICON_INTRO}\n\n{lexicon.strip()}")
    return "\n\n".join(sections)


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


def save_learned(text, path=DEFAULT_LEARNED_PATH):
    """Write his edits to what the Entity has learned. It is his memory of him; when he crosses
    something out it should stay crossed out."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def append_learned(facts, path=DEFAULT_LEARNED_PATH):
    if not facts:
        return
    path = Path(path)
    existing = _read(path).rstrip() or "# Learned about the user"
    body = existing + "\n" + "\n".join(f"- {fact}" for fact in facts) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def profile_sections(text):
    """The profile split by its "## " headings - what the window's Goals/Projects/Enhancements tabs
    render. {heading: body}; text before the first heading is dropped (it's the file's preamble)."""
    sections = {}
    heading = None
    lines = []
    for line in text.splitlines():
        if line.startswith("## "):
            if heading is not None:
                sections[heading] = "\n".join(lines).strip()
            heading = line[3:].strip()
            lines = []
        elif heading is not None:
            lines.append(line)
    if heading is not None:
        sections[heading] = "\n".join(lines).strip()
    return sections


ENHANCEMENTS_HEADING = "Enhancements he wants for you (roadmap, not now)"


def _merged(path, heading, body, keeping):
    """His edited body, plus any line the section has gained since he started editing it."""
    if keeping is None:
        return body
    current = profile_sections(_read(path)).get(heading, "")
    added = [line for line in current.splitlines()
             if line.strip() and line not in keeping.splitlines() and line not in body.splitlines()]
    return "\n".join([body.rstrip()] + added) if added else body


def append_enhancement(item, path=DEFAULT_PROFILE_PATH, heading=ENHANCEMENTS_HEADING):
    """File one enhancement bullet INSIDE its section, so the window's tab (which re-reads this
    file) shows it the moment it lands - not at the end of the file under some other heading."""
    path = Path(path)
    lines = _read(path).splitlines()
    insert_at = None
    inside = False
    for index, line in enumerate(lines):
        if line.startswith("## "):
            if inside:  # the section ended - the bullet goes just before this next heading
                insert_at = index
                break
            inside = line[3:].strip() == heading
    if inside and insert_at is None:  # the section runs to the end of the file
        insert_at = len(lines)
    if insert_at is None:  # no such section yet - start it at the end
        lines += ["", f"## {heading}"]
        insert_at = len(lines)
    while insert_at > 0 and not lines[insert_at - 1].strip():
        insert_at -= 1  # tuck the bullet against the section's last line, not after its blank gap
    lines.insert(insert_at, f"- {item}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def save_section(path, heading, body, *, keeping=None):
    """Replace one "## heading" section's body, leaving every other line of the file untouched.

    This is his own profile - the same file the brain loads as standing context - so an edit made
    in the window has to be surgical: rewriting the whole file from parsed sections would quietly
    drop the preamble and reflow everything he didn't touch.

    `keeping` is what that section held when he started typing. Anything the file has gained since
    - a line Entity filed while his edit was in progress - is carried over instead of being
    overwritten, which is how one of its bullets ended up truncated mid-sentence.
    """
    body = _merged(path, heading, body, keeping)
    path = Path(path)
    lines = _read(path).splitlines()
    start = end = None
    for index, line in enumerate(lines):
        if not line.startswith("## "):
            continue
        if start is None and line[3:].strip() == heading:
            start = index + 1
        elif start is not None:
            end = index
            break
    body_lines = body.rstrip().splitlines()
    if start is None:  # no such section yet - start one at the end
        lines += ["", f"## {heading}"] + body_lines
    else:
        tail = lines[end:] if end is not None else []
        lines = lines[:start] + body_lines + ([""] if tail else []) + tail
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
