"""The first fleet experiment: launch supervised agents in the 8 Notecraft worktrees and let
the user manage them by voice.

    python -m entity.fleet_experiment          # manage by voice
    python -m entity.fleet_experiment --text   # manage by keyboard (dry run)

Each agent works approval-gated: it proposes an action, the Entity relays it to the user, and
only proceeds on his yes. When several are waiting it reads them out and lets him pick the order.
"""

import sys
from pathlib import Path

from entity.fleet import FleetSupervisor
from entity.fleet_io import ConsoleFleetIO, VoiceFleetIO
from entity.fleet_runner import Fleet
from entity.fleet_session import run_fleet
from entity.supervised_agent import SupervisedAgent

WORKTREES_DIR = Path.home() / "workspace" / "notecraft" / ".claude" / "worktrees"
EXPERIMENT_WORKTREES = (
    "the-tracker-note-destination-4d4b7c",
    "the-tracker-transcript-naming-c26fb0",
    "audio-modal-selection-delete-102dbc",
    "dreamy-stonebraker-b40a4c",
    "note-grouping-checkboxes-240395",
    "notes-grouping-interaction-81993d",
    "refresh-bin-back-buttons-73ca9c",
    "voice-transcription-fallback-e8b530",
)
TASK = (
    "You are in a git worktree for a Notecraft feature. Look at the branch name and the working "
    "tree, work out what this feature is meant to do, and continue it. You'll be asked to approve "
    "anything that changes files or runs commands, so go ahead and propose your next action."
)


def _build_io(text_mode):
    if text_mode:
        return ConsoleFleetIO()
    from entity.mic import Microphone
    from entity.stt_mic import MicSTT
    from entity.transcribe import ParakeetTranscriber
    from entity.tts_system import SystemTTS

    transcriber = ParakeetTranscriber()
    transcriber.warmup()
    return VoiceFleetIO(speak=SystemTTS(rate=2).speak, listen=MicSTT(transcriber, Microphone()).listen)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    text_mode = "--text" in argv

    print("Waking the fleet...")
    fleet = Fleet(FleetSupervisor())
    agents, tasks = {}, {}
    for name in EXPERIMENT_WORKTREES:
        agents[name] = SupervisedAgent(name, str(WORKTREES_DIR / name), fleet.decide)
        tasks[name] = TASK

    io = _build_io(text_mode)
    try:
        run_fleet(agents, tasks, fleet, io)
    finally:
        for agent in agents.values():
            try:
                agent.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
