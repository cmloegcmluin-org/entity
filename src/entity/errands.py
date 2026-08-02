"""Small local chores, run quietly - no agent tab for every little thing.

"I just don't want my agents log tab to be cluttered with an agent for every little thing I ask
it to do, rather than more like one agent per actual major task." The fast brain deliberately has
no file tools - that is part of why it answers in a breath - so the little jobs go to another
part of the brain: one quiet helper session with file tools and nothing else, no desk entry, no
tab, no worktree. It does the chore, reports one sentence, and the narrator words the outcome in
Excephalon's own voice like any other news.

Real work still goes to real agents: this runs errands, it does not build features.
"""

import threading

from claude_agent_sdk import ClaudeAgentOptions

from entity.models import FAMILIES
from entity.sdk_session import SdkSession

ERRAND_MODEL = FAMILIES["haiku"]  # fetch-and-carry work: the smallest, fastest tier

PROMPT = (
    "[Errand from Excephalon on the user's behalf - no user is in this exchange. Do this small local "
    "chore now, using your tools, and reply with ONE short plain sentence saying what you did - "
    "or exactly what stopped you:\n{chore}]"
)


def _errand_options(cwd):
    return ClaudeAgentOptions(
        cwd=str(cwd),
        model=ERRAND_MODEL,
        # File tools and a shell: enough for moving, tidying, reading, renaming. The user runs
        # whole coding agents unattended by choice; a chore hand needs no more ceremony.
        allowed_tools=["Read", "Write", "Edit", "Glob", "Grep", "Bash"],
        permission_mode="bypassPermissions",
        setting_sources=[],
    )


class ErrandRunner:
    """One quiet helper session, opened on first use, reused for every chore after."""

    def __init__(self, cwd, events, *, session_factory=SdkSession):
        self._cwd = cwd
        self._events = events  # (kind, agent, report) - the same sink the desk's news takes
        self._session_factory = session_factory
        self._session = None
        self._lock = threading.Lock()

    def run(self, chore):
        """Take one chore. Returns at once; the outcome arrives as an "errand" event, worded by
        the narrator - so the user hears one sentence in Excephalon's voice, not a tool transcript."""
        threading.Thread(target=self._work, args=(chore,), daemon=True).start()

    def _work(self, chore):
        try:
            said = self._ensure_session().ask(PROMPT.format(chore=chore))
        except Exception as exc:
            # A chore that silently evaporated would be the lost-agent failure in miniature.
            self._events("errand", "errands", f"the errand could not run: {exc}")
            return
        self._events("errand", "errands", said.strip() or "(finished without a word)")

    def _ensure_session(self):
        with self._lock:
            if self._session is None:
                self._session = self._session_factory(_errand_options(self._cwd))
            return self._session

    def close(self):
        with self._lock:
            session, self._session = self._session, None
        if session is not None:
            try:
                session.close()
            except Exception:
                pass  # a session already gone must not block shutdown
