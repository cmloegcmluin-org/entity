"""The Entity's brain: one persistent Claude session via the Agent SDK.

Keeping a single warm session - instead of re-spawning the `claude` CLI every turn -
drops per-turn latency from ~5s to ~1.7s. It runs on the Max subscription:
allowed_tools=[] keeps the context lean, and setting_sources without "user" keeps the
OAuth login while dropping the global coding instructions so it talks like a companion.

The SDK is async; SdkBrain runs it on a private background event loop so the rest of the
app keeps the plain synchronous `respond(text) -> text` interface.
"""

import asyncio
import threading

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage

DEFAULT_PERSONA = (
    "You are Entity, the user's voice companion. You pair with him on his life the way a good "
    "pair-programming partner works: present, steady, and concise. Speak in short, natural spoken "
    "sentences - no markdown, no bullet lists, no emoji, usually one to three sentences. Ask one "
    "question at a time. You help him think, plan, and take the next small step. You are not a "
    "therapist and you give no medical or clinical advice; when something is heavy, listen briefly "
    "and steer back to what is actionable. When you do not know, say so plainly."
)


def _extract_text(messages):
    """Concatenate the text of every TextBlock across the assistant's response messages."""
    text = ""
    for message in messages:
        for block in getattr(message, "content", ()) or ():
            value = getattr(block, "text", None)
            if isinstance(value, str):
                text += value
    return text.strip()


class SdkBrain:
    def __init__(self, *, persona=DEFAULT_PERSONA, model="sonnet", setting_sources=("project", "local")):
        self._options = ClaudeAgentOptions(
            system_prompt=persona,
            allowed_tools=[],
            setting_sources=list(setting_sources),
            model=model,
        )
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._client = ClaudeSDKClient(options=self._options)
        self._submit(self._client.connect())

    def _submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    async def _ask(self, utterance):
        await self._client.query(utterance)
        messages = []
        async for message in self._client.receive_response():
            messages.append(message)
            if isinstance(message, ResultMessage):
                break
        return _extract_text(messages)

    def respond(self, utterance):
        return self._submit(self._ask(utterance))

    def warmup(self):
        """Pay the variable cold-start of the first query now (session spin-up), so the
        user's first real turn is fast. The reply is discarded."""
        self._submit(self._ask("Reply with just: ready"))

    def close(self):
        try:
            self._submit(self._client.disconnect())
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
