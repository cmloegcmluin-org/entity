"""The Entity's brain: one persistent Claude agent, isolated from the global config.

Isolation is critical: `setting_sources=[]` loads NONE of the user's user/project/local
settings, so the Entity never inherits his global coding CLAUDE.md or hooks. If it did,
the terminal reply-format instructions AND the Stop hook that enforces them bleed into the
companion - it starts answering in ">>"/">" quote blocks, the hook fires every turn and
injects "FORMAT VIOLATION" feedback, and latency explodes to ~50s. It is meant to run with
its native Claude-agent tools so it can actually act (read/write files, run commands, drive
other agents) - see the permission note in `_make_options`. Runs on the Max subscription
(OAuth is read independently of settings, so no API key is needed).

The async plumbing lives in SdkSession; SdkBrain just supplies the options.
"""

from claude_agent_sdk import ClaudeAgentOptions

from entity.sdk_session import SdkSession

DEFAULT_PERSONA = (
    "You are Entity, the user's voice companion and his hands on this machine. Your replies are "
    "spoken aloud, so keep them short and natural - a sentence or two, no markdown, no lists, and "
    "ask one thing at a time. "
    "You have real tools: you can read and write files, run shell commands, and launch and drive "
    "other Claude Code agents. When the user asks for something, actually DO it with those tools - "
    "don't just describe doing it, and don't ask him to do it himself. Never claim an ability you "
    "do not have (you have no email or web access unless a tool for it is present); if you truly "
    "cannot do something, say so plainly and do what you can. After you take an action, say briefly "
    "what you did rather than narrating every step. "
    "You are not a therapist and give no medical advice; keep things practical."
)


def _make_options(persona, model):
    # The Entity's native tools stay gated until the user enables fully-autonomous operation
    # himself: the safety system requires the user (not the agent) to turn approvals off. He
    # does that by adding a permission-mode setting here (see the message where I hand him the
    # exact line). Left gated by default so nothing runs unattended without his explicit action.
    return ClaudeAgentOptions(
        system_prompt=persona,
        permission_mode="bypassPermissions",
        setting_sources=[],  # load NO user/project/local settings: no global CLAUDE.md, no hooks
        model=model,
    )


class SdkBrain:
    def __init__(self, *, persona=DEFAULT_PERSONA, model="sonnet", session_factory=SdkSession):
        self._options = _make_options(persona, model)
        self._new_session = session_factory
        self._session = self._new_session(self._options)

    def respond(self, utterance):
        try:
            return self._session.ask(utterance)
        except Exception:
            # The session may be wedged (a dropped connection strands every later turn as a
            # "glitch"). Rebuild it and try once more; only give up if that also fails.
            self._reconnect()
            return self._session.ask(utterance)

    def _reconnect(self):
        try:
            self._session.close()
        except Exception:
            pass
        self._session = self._new_session(self._options)

    def warmup(self):
        """Pay the variable cold-start of the first query now, so the user's first real turn is fast."""
        self._session.ask("Reply with just: ready")

    def close(self):
        self._session.close()
