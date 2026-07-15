"""The Entity's brain: one persistent, isolated Claude session (companion persona, no tools).

Isolation is critical: `setting_sources=[]` loads NONE of the user's user/project/local
settings, so the Entity never inherits his global coding CLAUDE.md or hooks. If it did,
the terminal reply-format instructions AND the Stop hook that enforces them bleed into the
companion - it starts answering in ">>"/">" quote blocks, the hook fires every turn and
injects "FORMAT VIOLATION" feedback, and latency explodes to ~50s. `allowed_tools=[]`
likewise keeps the context lean. Runs on the Max subscription (OAuth is read independently
of settings, so no API key is needed).

The async plumbing lives in SdkSession; SdkBrain just supplies the companion options.
"""

from claude_agent_sdk import ClaudeAgentOptions

from entity.sdk_session import SdkSession

DEFAULT_PERSONA = (
    "You are Entity, the user's voice companion. You pair with him on his life the way a good "
    "pair-programming partner works: present, steady, and concise. Speak in short, natural spoken "
    "sentences - no markdown, no bullet lists, no emoji, usually one to three sentences. Ask one "
    "question at a time. You help him think, plan, and take the next small step. You are not a "
    "therapist and you give no medical or clinical advice; when something is heavy, listen briefly "
    "and steer back to what is actionable. When you do not know, say so plainly."
)


def _make_options(persona, model):
    return ClaudeAgentOptions(
        system_prompt=persona,
        allowed_tools=[],
        setting_sources=[],  # load NO user/project/local settings: no global CLAUDE.md, no hooks
        model=model,
    )


class SdkBrain:
    def __init__(self, *, persona=DEFAULT_PERSONA, model="sonnet"):
        self._session = SdkSession(_make_options(persona, model))

    def respond(self, utterance):
        return self._session.ask(utterance)

    def warmup(self):
        """Pay the variable cold-start of the first query now, so the user's first real turn is fast."""
        self._session.ask("Reply with just: ready")

    def close(self):
        self._session.close()
