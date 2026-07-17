"""Cheapest local robot voice for v1: Windows System.Speech, driven via PowerShell.

No pip dependencies. The text to speak is passed through an environment variable, so it is never
interpolated into the command string - no quoting, escaping, or injection surface regardless of
what the brain says. Speaking is interruptible: pass an `interrupt` Event and the voice is killed
the moment it fires, so the user can cut off a runaway reply instead of sitting through it.
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


def _default_ps_run(script, text, interrupt=None):
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if exe is None:
        raise TTSError("could not find PowerShell to drive System.Speech")
    env = {**os.environ, "ENTITY_TTS_TEXT": text}
    proc = subprocess.Popen(
        [exe, "-NoProfile", "-NonInteractive", "-Command", script],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    while True:
        try:
            proc.wait(timeout=0.05)  # check often enough to fall silent within a breath of a cut-in
            break
        except subprocess.TimeoutExpired:
            if interrupt is not None and interrupt.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return  # he cut it off - not a failure
    if proc.returncode != 0:
        raise TTSError(f"System.Speech failed: {(proc.stderr.read() if proc.stderr else '').strip()}")


class NullTTS:
    """Speaks nothing - for muted / text-only runs."""

    def speak(self, text, *, interrupt=None):
        pass


class SystemTTS:
    def __init__(self, *, rate=0, run=_default_ps_run):
        self._script = _SPEAK_SCRIPT.format(rate=rate)
        self._run = run

    def speak(self, text, *, interrupt=None):
        if not text.strip():
            return
        self._run(self._script, text, interrupt)
