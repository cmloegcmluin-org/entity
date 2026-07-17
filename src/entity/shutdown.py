"""Ending a session cleanly and quickly.

Quitting used to hang for the better part of a minute: the end-of-session memory-consolidation is a
full brain turn, and nothing bounded it. `consolidate` runs it on a worker thread and gives up if it
overruns, so saying goodbye never waits on a slow (or wedged) model - at worst this session's learned
facts aren't saved, which beats a minute of dead air on the way out.
"""

import threading

from entity.memory import CONSOLIDATION_PROMPT, parse_facts

DEFAULT_CONSOLIDATION_TIMEOUT = 15.0


def consolidate(brain, *, timeout=DEFAULT_CONSOLIDATION_TIMEOUT, prompt=CONSOLIDATION_PROMPT):
    """Ask the brain what new, durable facts came up, but never let it stall the exit: if the reply
    doesn't land within `timeout`, give up and save nothing. Returns the parsed facts (maybe empty).
    A brain error is swallowed for the same reason - shutdown must not depend on the brain behaving."""
    result = {}

    def work():
        try:
            result["reply"] = brain.respond(prompt)
        except Exception:
            pass  # a wedged/closing session must not crash the exit

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(timeout)
    return parse_facts(result["reply"]) if "reply" in result else []
