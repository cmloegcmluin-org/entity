"""What an agent actually DID, not just what it narrated.

An agent's log used to be written from its text alone, so a session showed the sentences it
happened to say between steps - "Confirmed red. Now the implementation:" - and then ten silent
minutes. Everything that would let anyone judge the work was dropped before it reached the file:
the commands, the test output, the diffs. Asked for the real exchange repeatedly and given a
narrated summary each time, the point was never logging: it was being able to compare how the
Excephalon drives an agent with how they would have driven it themselves.

So every message the agent streams back is rendered here, as the lines its log shows:

    Bash: python -m pytest -q          a call, at the margin, named by what it acts on
        358 passed in 4.41s            what it gave back, indented under it
    Edit: src/entity/steps.py          a change, shown as the change
        - latest = ""
        + lines = []
        ! fatal: not a git repository  a call that failed, so that reads differently from output

Two kinds, because the tab draws them differently: SAID is the agent talking (its own messages),
DID is the machinery under it. Thinking is not rendered - it is not part of the exchange, and it
would double the size of every log for something nobody asked to see.

The caps below are the one concession: the tab has to stay something a person reads. Nothing is
ever dropped silently - what went is counted out loud, in place.
"""

SAID = "said"
DID = "did"

INDENT = "    "  # everything a call produced sits under it; only call lines start at the margin

# How much of one result is kept: both ends, because a run says what it is doing at the top and
# how it came out at the bottom, and the middle is counted rather than quietly lost.
HEAD_LINES = 40
TAIL_LINES = 12

# A minified blob or a base64 payload arrives as ONE line, which no line-count cap ever touches.
# Only ever applied to the machinery: the agent's own words are the part being read, and cutting
# a sentence of theirs mid-word would be the missing evidence this whole module exists to end.
LINE_CHARS = 400

# What a call is ABOUT, tool by tool: the command it runs, the file it touches, the thing it looks
# for. Tried in order, because a tool can carry several of these and the first is the specific one.
_SUBJECT_KEYS = ("command", "file_path", "pattern", "path", "url", "prompt", "query")


def render(message):
    """One message from an agent, as the (kind, text) lines its log should show."""
    content = getattr(message, "content", None)
    if not isinstance(content, (list, tuple)):
        # A message can carry its content as one plain string - the prompt the desk itself just
        # sent, and already logged. Iterating that would walk it a character at a time.
        return []
    lines = []
    for block in content:
        if isinstance(getattr(block, "text", None), str):
            if block.text.strip():
                lines.append((SAID, block.text.strip()))
        elif getattr(block, "name", None) and hasattr(block, "input"):
            lines.extend(_machinery([_headline(block)] + _change(block.input)))
        elif hasattr(block, "tool_use_id"):
            lines.extend(_machinery(_under(_text_of(block.content),
                                           failed=bool(getattr(block, "is_error", False)))))
    return lines


def _machinery(texts):
    return [(DID, _cut(text)) for text in texts]


def _headline(call):
    """The call itself: the tool, and what it acted on. Not every tool acts on a path or a command,
    and a dangling "TodoWrite: " reads as a target that went missing rather than one that never
    existed - so a tool with nothing to name is written as just its name."""
    for key in _SUBJECT_KEYS:
        value = call.input.get(key)
        if isinstance(value, str) and value.strip():
            return f"{call.name}: {value.strip()}"
    return str(call.name)


def _change(tool_input):
    """What a call did to a file, shown as the change itself - the whole point of an edit. A file
    written from scratch is a change too: all of it added."""
    old = tool_input.get("old_string")
    new = tool_input.get("new_string", tool_input.get("content"))
    if not isinstance(old, str) and not isinstance(new, str):
        return []
    # Capped a side at a time, so a big deletion can't swallow the addition that replaced it.
    return (_capped([f"{INDENT}- {line}" for line in str(old or "").splitlines()])
            + _capped([f"{INDENT}+ {line}" for line in str(new or "").splitlines()]))


def _under(text, *, failed=False):
    """Output, sat under the call that produced it. A blank line stays blank: indenting emptiness
    only writes whitespace nobody can see. A failure is marked, because whether a command actually
    worked is the first thing anyone reads a log for, and an error that looks exactly like output
    answers that question wrong."""
    gutter = f"{INDENT}! " if failed else INDENT
    return _capped([f"{gutter}{line}" if line.strip() else "" for line in str(text).splitlines()])


def _text_of(content):
    """A result's text, however it arrived. The SDK hands one back either as a plain string or as
    a list of blocks, and understanding only one of those shapes drops whole commands on the floor.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict))


def _capped(lines):
    if len(lines) <= HEAD_LINES + TAIL_LINES + 1:
        return lines
    dropped = len(lines) - HEAD_LINES - TAIL_LINES
    return lines[:HEAD_LINES] + [f"{INDENT}… {dropped} more lines …"] + lines[-TAIL_LINES:]


def _cut(line):
    return line if len(line) <= LINE_CHARS else line[:LINE_CHARS] + "…"
