"""A persistent Claude session held open on a private background event loop.

The SDK is async; this wraps one `ClaudeSDKClient` so the rest of the app can talk to it with
a plain synchronous `ask(prompt) -> text`. Shared by the companion brain (`SdkBrain`) and the
supervised coding agents (`SupervisedAgent`) - they differ only in the options they pass in.
"""

import asyncio
import threading

from claude_agent_sdk import ClaudeSDKClient, ResultMessage


def extract_text(messages):
    """The spoken reply is the FINAL thing the Entity says, not its running narration.

    A tool-using turn emits text between every step ("Now let me read that file...", "Found it,
    let me check..."); only the last message is the actual answer. Reading all of it aloud dumps the
    play-by-play the user is supposed to be insulated from, so we keep just the last message that has
    text and drop the narration before it."""
    latest = ""
    for message in messages:
        text = ""
        for block in getattr(message, "content", ()) or ():
            value = getattr(block, "text", None)
            if isinstance(value, str):
                text += value
        if text.strip():
            latest = text
    return latest.strip()


def _context_tokens(usage):
    """How many tokens the model just processed as input = fresh input + both cache tiers. This
    is what grows as a conversation runs on and what makes each turn slower, so it's the number
    the brain watches to decide when to compact. Output tokens are excluded - they aren't context
    the next turn re-reads."""
    if not usage:
        return 0
    return sum(
        int(usage.get(key, 0) or 0)
        for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    )


class SdkSession:
    def __init__(self, options):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._client = ClaudeSDKClient(options=options)
        self._submit(self._client.connect())
        self.last_context_tokens = 0  # size of the context the most recent ask processed

    def _submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    async def _ask(self, prompt):
        await self._client.query(prompt)
        messages = []
        async for message in self._client.receive_response():
            messages.append(message)
            if isinstance(message, ResultMessage):
                self.last_context_tokens = _context_tokens(message.usage)
                break
        return extract_text(messages)

    def ask(self, prompt):
        return self._submit(self._ask(prompt))

    def close(self):
        try:
            self._submit(self._client.disconnect())
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
