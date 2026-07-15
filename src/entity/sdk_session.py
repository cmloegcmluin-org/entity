"""A persistent Claude session held open on a private background event loop.

The SDK is async; this wraps one `ClaudeSDKClient` so the rest of the app can talk to it with
a plain synchronous `ask(prompt) -> text`. Shared by the companion brain (`SdkBrain`) and the
supervised coding agents (`SupervisedAgent`) - they differ only in the options they pass in.
"""

import asyncio
import threading

from claude_agent_sdk import ClaudeSDKClient, ResultMessage


def extract_text(messages):
    """Concatenate the text of every TextBlock across the assistant's response messages."""
    text = ""
    for message in messages:
        for block in getattr(message, "content", ()) or ():
            value = getattr(block, "text", None)
            if isinstance(value, str):
                text += value
    return text.strip()


class SdkSession:
    def __init__(self, options):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._client = ClaudeSDKClient(options=options)
        self._submit(self._client.connect())

    def _submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    async def _ask(self, prompt):
        await self._client.query(prompt)
        messages = []
        async for message in self._client.receive_response():
            messages.append(message)
            if isinstance(message, ResultMessage):
                break
        return extract_text(messages)

    def ask(self, prompt):
        return self._submit(self._ask(prompt))

    def close(self):
        try:
            self._submit(self._client.disconnect())
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
