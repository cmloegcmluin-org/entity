"""What an agent is allowed to say to the user: a notice, never its own words.

They were handed commit hashes, test counts and "I reran the suite myself" verbatim, and could not
tell whether they were talking to the Entity or to the agent it was driving. Telling the model not to
relay was not enough - they asked for the code to prevent it - so nothing an agent writes reaches the
outbox except this: who spoke, and its first sentence, capped. The whole exchange stays in that
agent's tab, where reading it is their choice.
"""

import re

NOTICE_CHARS = 160  # a sentence's worth; past this it is the agent talking, not a notice

_SENTENCE_END = re.compile(r"(?<=[.!?])\s")


def notice(agent, report):
    """One line they can act on: which agent, and the gist, with the rest left in its tab."""
    said = " ".join(str(report).split())
    if not said:
        return f"{agent} sent an empty report."
    first = _SENTENCE_END.split(said, maxsplit=1)[0]
    if len(first) > NOTICE_CHARS:
        first = first[:NOTICE_CHARS].rstrip() + "…"
    more = "" if first == said else f" (the rest is in {agent}'s tab)"
    return f"{agent}: {first}{more}"
