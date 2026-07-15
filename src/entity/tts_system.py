"""Cheapest local robot voice for v1: Windows System.Speech, driven via PowerShell.

No pip dependencies. The text to speak is passed through an environment variable, so
it is never interpolated into the command string - no quoting, escaping, or injection
surface regardless of what the brain says.
"""

import os
import shutil
import subprocess

_SPEAK_SCRIPT = (
    "Add-Type -AssemblyName System.Speech; "
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
    "$s.Rate = {rate}; "
    "$s.Speak($env:ENTITY_TTS_TEXT)"
)


class TTSError(RuntimeError):
    pass


def _default_ps_run(script, text):
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if exe is None:
        raise TTSError("could not find PowerShell to drive System.Speech")
    env = {**os.environ, "ENTITY_TTS_TEXT": text}
    proc = subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-Command", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise TTSError(f"System.Speech failed: {proc.stderr.strip()}")


class NullTTS:
    """Speaks nothing - for muted / text-only runs."""

    def speak(self, text):
        pass


class SystemTTS:
    def __init__(self, *, rate=0, run=_default_ps_run):
        self._script = _SPEAK_SCRIPT.format(rate=rate)
        self._run = run

    def speak(self, text):
        if not text.strip():
            return
        self._run(self._script, text)
