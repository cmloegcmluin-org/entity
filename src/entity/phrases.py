"""Matching the spoken control phrases - shared by the conversation loop and the dictation mic.

One home for these, because two implementations would drift: the conversation's sleep/wake matching
already went through three rounds of hard-won loosening (punctuation, stray leading/trailing words),
and the windowed dictation mode needs exactly the same tolerance for the same phrases.
"""

import re


def canonical(text):
    """Lowercase, strip punctuation, collapse whitespace — so 'Goodbye, Entity.' matches 'goodbye entity'."""
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def ends_with_command(canonical_text, commands):
    """A command counts if the utterance IS it or ENDS with it - so "okay, stop listening" trips
    "stop listening", not just the bare phrase. (They rarely say these distinctive phrases by
    accident, and transcription usually tacks a stray word on, which exact-match then missed.)"""
    return any(canonical_text == cmd or canonical_text.endswith(" " + cmd) for cmd in commands)


def wakes(canonical_text, commands):
    """A wake word also counts at the START. "Hey Entity, can you hear me?" is plainly them waking it,
    but it ENDS on "hear me" - so an ends-with check left them saying it over and over until they
    happened to say the bare phrase alone."""
    return ends_with_command(canonical_text, commands) or any(
        canonical_text.startswith(cmd + " ") for cmd in commands
    )


def strip_leading_command(canonical_text, commands):
    """The rest of the utterance after a command it STARTS with ("hey entity add milk" -> "add
    milk"), or None if it doesn't start with one. Lets a wake phrase carry its first real words."""
    for cmd in commands:
        if canonical_text == cmd:
            return ""
        if canonical_text.startswith(cmd + " "):
            return canonical_text[len(cmd) + 1:]
    return None
