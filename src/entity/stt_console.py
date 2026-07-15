"""Typed-input stand-in for speech-to-text: read a line from the console.

Lets the whole listen -> think -> speak loop run (and be verified) before the
microphone path (Parakeet) is wired in, and stays useful as a text fallback.
"""


class ConsoleSTT:
    def __init__(self, *, prompt="you> ", read=input, eof_utterance="goodbye entity"):
        self._prompt = prompt
        self._read = read
        self._eof_utterance = eof_utterance

    def listen(self):
        try:
            return self._read(self._prompt)
        except EOFError:
            return self._eof_utterance
