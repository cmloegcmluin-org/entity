"""Ending a session cleanly and quickly.

Quitting used to hang for the better part of a minute: the end-of-session memory-consolidation is a
full brain turn, and nothing bounded it. `consolidate` runs it on a worker thread and gives up if it
overruns, so saying goodbye never waits on a slow (or wedged) model - at worst this session's learned
facts aren't saved, which beats a minute of dead air on the way out.
"""

import threading

from excephalon.memory import CONSOLIDATION_PROMPT, parse_facts

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


def leave_process(*, exit=None):
    """End the process without interpreter finalization - the last line of a clean wind-down.

    "Python quit unexpectedly" on every close: the crash report shows the main thread inside
    Py_Finalize unloading modules while a daemon audio thread was mid-call into native code whose
    pages were already unmapped. The mic pump and playback are daemon threads by design - they
    cannot all be joined - and everything durable is already on disk before this line (the
    transcript writes per line, the fleet record on the way down, consolidation just above). So
    interpreter teardown buys nothing but the segfault, and the process leaves the way native-
    audio GUI apps do: immediately."""
    import os

    (exit or os._exit)(0)
