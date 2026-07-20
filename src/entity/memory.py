"""The Entity's memory of its user.

Three layers, all under the gitignored `runtime/` dir (private):
- `profile.md`  - the hand-written standing profile (goals, projects, life context). Its title
                  line is also what the Entity calls its user - see `user_name`.
- `learned.md`  - facts the Entity captured itself from past conversations.
- `lexicon.md`  - the user's working vocabulary: names they coined (Notecraft, WaveShaper) AND the
                  domain terms and proper nouns of the fields they work in (Bayesian inference,
                  the people they collaborate with) - one word or several. Triple duty: it is part
                  of the brain's standing context so it knows their words, transcription here is
                  biased toward the same list (see `vocabulary`), and another tool that
                  transcribes the same person can correct against it too - so teaching a term once
                  fixes all three. That last duty is why the file may live outside this repo
                  entirely; `lexicon_path` is how it says where.

All are folded into the brain's system prompt at startup, so it knows the user without being
re-told. At the end of a session the brain is asked what new, durable facts came up; those get
appended to `learned.md`, so next time it remembers them too - the auto-capture-and-remember loop.
"""

import re
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parents[2] / "runtime"
DEFAULT_PROFILE_PATH = _RUNTIME / "profile.md"
DEFAULT_LEARNED_PATH = _RUNTIME / "learned.md"
DEFAULT_LEXICON_PATH = _RUNTIME / "lexicon.md"
# One line naming the lexicon file, when it isn't the one above - see `lexicon_path`.
LEXICON_POINTER = _RUNTIME / "lexicon-path.txt"

# `{user}` is filled in from the profile's own title line by `compose_persona` - see `user_name`.
USER_PLACEHOLDER = "{user}"

_PREAMBLE = (
    "Here is standing context about {user}'s life, for your awareness only. Do NOT raise any of "
    "it unprompted, and do not turn into a therapist or life-coach about it. "
    "Use it only to be more useful and less clueless when they bring something up themselves:"
)

_LEXICON_INTRO = (
    "This is {user}'s working vocabulary - not only names they coined, but the domain terms, proper "
    "nouns and terms of art of the fields they work in (their projects, the subjects they study, the "
    "people they work with). Recognize them when they use them, and get them "
    "right when you use them back - their speech-to-text is biased toward this same list. Don't force "
    "them into the conversation:"
)

# A gloss can follow the term after " - " / " — " / ": "; the term itself is the head of the line.
_GLOSS = re.compile(r"\s+[—–-]\s+|:\s+")

CONSOLIDATION_PROMPT = (
    "Our conversation is ending. List, as short bullet points (each starting with '-'), any NEW and "
    "durable facts about the user that came up and are worth remembering in future sessions - "
    "decisions, preferences, life updates, commitments - and that you didn't already know about them. "
    "Only things that will still matter later. If there is nothing new worth saving, reply with "
    "exactly: none"
)


ANONYMOUS_USER = "the user"


def user_name(profile, default=ANONYMOUS_USER):
    """What to call the person the Entity is for, taken from the title line of their own profile
    ("# Ada - standing profile" -> "Ada"), with any gloss after the title dropped.

    The name belongs to the user, so it is read from the user's file rather than written into the
    source. A checkout with no profile yet still has to compose sentences, hence the neutral
    default: every persona line reads the same whether it says a name or "the user"."""
    for line in profile.splitlines():
        if line.startswith("# "):
            return _GLOSS.split(line[2:].strip(), maxsplit=1)[0].strip() or default
    return default


def load_profile(path=DEFAULT_PROFILE_PATH):
    return _read(path)


def load_learned(path=DEFAULT_LEARNED_PATH):
    return _read(path)


def lexicon_path(pointer=LEXICON_POINTER, default=DEFAULT_LEXICON_PATH):
    """Which file the lexicon is. Beside the rest of the runtime state, unless `lexicon-path.txt`
    names somewhere else.

    The point of the indirection is that this list is worth sharing: whatever else transcribes
    this user - a note-taker, a memo app, another machine - wants the same terms, and a term
    taught once should fix all of them. That shared copy lives wherever the tool that syncs it
    keeps it, which is nowhere this repo can guess, so the user writes the path down instead."""
    try:
        target = pointer.read_text(encoding="utf-8").strip()
    except OSError:
        return default
    return Path(target).expanduser() if target else default


def load_lexicon(path=None):
    return _read(lexicon_path() if path is None else path)


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
    """Fold the user's standing context into the brain's system prompt: life context (profile +
    learned) under a do-not-play-therapist warning, and their lexicon under its own
    recognize-these framing.

    This is also where the persona learns whose companion it is: every `{user}` in the assembled
    text becomes the name from the profile. Substituting here rather than at each template keeps
    one place that can leave a placeholder showing - and the window renders this exact text."""
    life = "\n\n".join(section.strip() for section in (profile, learned) if section.strip())
    sections = [base_persona]
    if life:
        sections.append(f"{_PREAMBLE}\n\n{life}")
    if lexicon.strip():
        sections.append(f"{_LEXICON_INTRO}\n\n{lexicon.strip()}")
    return "\n\n".join(sections).replace(USER_PLACEHOLDER, user_name(profile))


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
    """Write the user's edits to what the Entity has learned. It is a memory OF them and it is
    theirs; when they cross something out it should stay crossed out."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def append_learned(facts, path=DEFAULT_LEARNED_PATH):
    if not facts:
        return
    path = Path(path)
    existing = _read(path).rstrip() or "# Learned in past sessions"
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


# Only the stem of the heading: a profile writes its own, and they run on ("Enhancements you want
# (roadmap, not now)"). Every reader matches on the stem - see `find_heading`.
ENHANCEMENTS_HEADING = "Enhancements"

# The enhancements list is a CHECKLIST: an item that gets done is ticked, never removed. "As you
# check items off from the enhancements list, I don't want them deleted forever." A struck-through
# item is also the only record that a complaint was heard and acted on - deleting it loses both the
# ask and the answer, and the same thing then gets filed again (five separate tickets in that list
# are one bug, refiled because nothing ever showed it had been dealt with).
#
# The list predates the boxes, so a plain "- item" is read as an unticked one and upgraded the first
# time anything writes it back. That migrates the file by use rather than by rewriting, under him,
# a personal file the running app may be autosaving at the same moment.
_BULLET = re.compile(r"^(\s*)[-*]\s+(?:\[(?P<tick>[ xX])\]\s+)?(?P<item>.*)$")
UNTICKED, TICKED = "- [ ] ", "- [x] "


def find_heading(sections, stem):
    """Which of the profile's own headings this stem means, or the stem itself if it has none yet.

    The profile is hand-written, so its headings carry whatever gloss their author wanted. Matching
    a whole heading line would miss the section that is plainly right there - and a filing that
    misses doesn't fail, it starts a rival section beside the real one."""
    lowered = stem.lower()
    return next((h for h in sections if h.lower().startswith(lowered)), stem)


def _merged(path, heading, body, keeping):
    """Their edited body, plus any line the section has gained since they started editing it."""
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
    text = _read(path)
    heading = find_heading(profile_sections(text), heading)
    lines = text.splitlines()
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
    lines.insert(insert_at, UNTICKED + item)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def complete_enhancement(item, path=DEFAULT_PROFILE_PATH, heading=ENHANCEMENTS_HEADING):
    """Tick the enhancement whose text contains `item`, in place. True if one was found.

    Matched loosely and on the first hit only, because the caller is quoting a fragment of a line
    the user wrote in their own words and at their own length. Reporting a miss matters: a tick that
    silently lands nowhere reads as done and isn't."""
    path = Path(path)
    text = _read(path)
    heading = find_heading(profile_sections(text), heading)
    wanted = item.strip().lower()
    lines = text.splitlines()
    inside = False
    for index, line in enumerate(lines):
        if line.startswith("## "):
            if inside:
                break  # the section ended; an item further down the file is not this list's
            inside = line[3:].strip() == heading
            continue
        match = _BULLET.match(line) if inside else None
        if match is None or (match.group("tick") or " ") != " ":
            continue  # not a bullet, or already ticked
        if wanted in match.group("item").strip().lower():
            lines[index] = TICKED + match.group("item").strip()
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return True
    return False


def save_section(path, heading, body, *, keeping=None):
    """Replace one "## heading" section's body, leaving every other line of the file untouched.

    This is their own profile - the same file the brain loads as standing context - so an edit made
    in the window has to be surgical: rewriting the whole file from parsed sections would quietly
    drop the preamble and reflow everything they didn't touch.

    `keeping` is what that section held when they started typing. Anything the file has gained since
    - a line Entity filed while their edit was in progress - is carried over instead of being
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
