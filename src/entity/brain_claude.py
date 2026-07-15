"""The Entity's brain: the local `claude` CLI, run headless once per turn.

Uses the Claude Max subscription (no separate API key). `--tools ""` strips the
coding-agent tool suite so replies come back in ~2s instead of ~15-60s, and
`--setting-sources project,local` keeps the OAuth login while dropping the global
coding instructions so the Entity talks like a companion, not a code reviewer.
"""

import json
import shutil
import subprocess

DEFAULT_PERSONA = (
    "You are Entity, the user's voice companion. You pair with him on his life the way a good "
    "pair-programming partner works: present, steady, and concise. Speak in short, natural spoken "
    "sentences - no markdown, no bullet lists, no emoji, usually one to three sentences. Ask one "
    "question at a time. You help him think, plan, and take the next small step. You are not a "
    "therapist and you give no medical or clinical advice; when something is heavy, listen briefly "
    "and steer back to what is actionable. When you do not know, say so plainly."
)


class BrainError(RuntimeError):
    """Raised when the claude CLI fails or returns an error result."""


def _default_run(cmd, stdin_text, cwd):
    # Resolve argv[0] to a full path so Windows can launch the `claude.CMD`/`.exe`
    # shim (a bare "claude" is not found by CreateProcess).
    resolved = shutil.which(cmd[0])
    if resolved is None:
        raise BrainError(f"could not find the `{cmd[0]}` CLI on PATH")
    proc = subprocess.run(
        [resolved, *cmd[1:]],
        input=stdin_text,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=120,
    )
    if proc.returncode != 0:
        raise BrainError(f"claude exited {proc.returncode}: {proc.stderr.strip()}")
    return proc.stdout


class ClaudeBrain:
    def __init__(
        self,
        *,
        persona=DEFAULT_PERSONA,
        model="sonnet",
        setting_sources="project,local",
        run=_default_run,
        cwd=None,
    ):
        self._persona = persona
        self._model = model
        self._setting_sources = setting_sources
        self._run = run
        self._cwd = cwd
        self._session_id = None

    def _command(self):
        cmd = [
            "claude",
            "-p",
            "--tools",
            "",
            "--setting-sources",
            self._setting_sources,
            "--system-prompt",
            self._persona,
            "--model",
            self._model,
            "--output-format",
            "json",
        ]
        if self._session_id:
            cmd += ["--resume", self._session_id]
        return cmd

    def respond(self, utterance):
        stdout = self._run(self._command(), utterance, self._cwd)
        data = json.loads(stdout)
        session_id = data.get("session_id")
        if session_id:
            self._session_id = session_id
        if data.get("is_error"):
            raise BrainError(data.get("result") or "claude returned an error")
        return (data.get("result") or "").strip()
