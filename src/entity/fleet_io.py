"""How the fleet loop talks to the user — by voice, or by console for a dry run.

Both back ends share the same tiny interface the loop calls: announce/pick/approve/report.
Parsing his spoken answers (a number to pick, a yes/no to approve) is pulled out into pure
helpers so it can be tested without a mic.
"""

import re

_YES = ("yes", "yeah", "yep", "approve", "sure", "go ahead", "okay", "ok", "do it", "allow")
_ORDINALS = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth")
# "one" is left out on purpose - it collides with "the voice one" / "that one"; use a digit or "first".
_NUMBER_WORDS = ("two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")


def _is_yes(text):
    lowered = text.strip().lower()
    return any(word in lowered for word in _YES)


def _choice_from(text, names):
    """Map a spoken answer to one of `names`: a digit, an ordinal, a number-word, or a name token."""
    lowered = text.strip().lower()
    digit = re.search(r"\d+", lowered)
    if digit:
        index = int(digit.group()) - 1
        if 0 <= index < len(names):
            return names[index]
    for index, word in enumerate(_ORDINALS):
        if index < len(names) and re.search(rf"\b{word}\b", lowered):
            return names[index]
    for offset, word in enumerate(_NUMBER_WORDS):
        index = offset + 1
        if index < len(names) and re.search(rf"\b{word}\b", lowered):
            return names[index]
    for name in names:
        tokens = [token for token in re.split(r"[-_]", name.lower()) if len(token) > 3]
        if any(re.search(rf"\b{re.escape(token)}\b", lowered) for token in tokens):
            return name
    return None


class ConsoleFleetIO:
    def announce(self, text):
        print(text)

    def report(self, agent, text):
        print(f"\n[{agent} finished]\n{text}\n")

    def pick(self, names):
        print("Ready for you: " + ", ".join(f"{i + 1}. {n}" for i, n in enumerate(names)))
        while True:
            choice = _choice_from(input("which one? "), names)
            if choice:
                return choice
            print("pick one: " + ", ".join(names))

    def approve(self, agent, request):
        return _is_yes(input(f"{agent} wants to {request} — approve? [y/n] "))


class VoiceFleetIO:
    def __init__(self, speak, listen):
        self._speak = speak
        self._listen = listen

    def announce(self, text):
        self._speak(text)

    def report(self, agent, text):
        self._speak(f"{agent} is done. {text}")

    def pick(self, names):
        listing = "; ".join(f"{i + 1}: {n}" for i, n in enumerate(names))
        self._speak(f"{len(names)} are ready. {listing}. Say the number.")
        while True:
            choice = _choice_from(self._listen(), names)
            if choice:
                return choice
            self._speak("Which number?")

    def approve(self, agent, request):
        self._speak(f"{agent} wants to {request}. Yes or no?")
        return _is_yes(self._listen())
