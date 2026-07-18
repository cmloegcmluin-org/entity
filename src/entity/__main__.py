"""Run the Entity: `python -m entity` (speak to it).

  --text        type instead of speaking
  --mute        show replies as text, don't speak them
  --no-timings  hide the per-turn think/speak readout (shown by default)
"""

import re
import signal
import sys
import threading
from datetime import datetime
from pathlib import Path

from entity.brain_sdk import DEFAULT_PERSONA, SdkBrain
from entity.console import Console
from entity.conversation import Conversation
from entity.fleet_io import ConsoleFleetIO, VoiceFleetIO
from entity.fleet_log import FleetLog
from entity.heartbeat import HeartbeatMonitor
from entity.inbox_watcher import InboxWatcher, QuietMonitor
from entity.memory import (
    append_learned,
    compose_persona,
    lexicon_terms,
    load_learned,
    load_lexicon,
    load_profile,
)
from entity.outbox import Outbox
from entity.shutdown import consolidate
from entity.stt_console import ConsoleSTT
from entity.supervising_brain import SupervisingBrain
from entity.tts_system import NullTTS, SystemTTS

RUNTIME_DIR = Path(__file__).resolve().parents[2] / "runtime"
AGENT_INBOX = RUNTIME_DIR / "agent-inbox"  # agents drop questions/review-ready notes here, one per line
FLEET_LOGS = RUNTIME_DIR / "fleet-logs"  # one timestamped transcript per driving session
MIC_OVERRIDE = RUNTIME_DIR / "mic.txt"  # optional: a device-name substring to force a specific mic
MIC_GAIN = RUNTIME_DIR / "mic-gain.txt"  # optional: a number to boost a quiet mic (e.g. 5)
VOCAB_ROOTS = RUNTIME_DIR / "vocab-roots.txt"  # optional: extra dirs (one per line) to mine for his project names
WORKSPACE = Path.home() / "workspace"  # his main project tree; its folder names seed the custom vocabulary
AGENT_QUIET_AFTER = 20 * 60  # seconds of silence from an agent before the Entity flags it to the user


def _make_fleet_log(target):
    """A fresh timestamped transcript for one driving session, named after what the user asked to drive."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(target).name).strip("-") or "session"
    return FleetLog(FLEET_LOGS / f"{slug}-{stamp}.log")


def _fresh_worktree_note():
    """Persona line: new work means a new worktree cut from freshly-fetched origin/main, not a stale
    local resume - the user said almost everything from here on starts a new worktree."""
    return (
        " Almost every agent you start is NEW work, which means a NEW worktree - don't resume an old "
        "one unless the user explicitly tells you to. When you set one up, base it on current "
        "origin/main: git fetch origin main first, then cut the worktree's branch from origin/main, "
        "so the agent never starts on stale local code that's fallen behind what's already merged."
    )


def _mic_gain():
    """How much to amplify the mic. A quiet input (his onboard mic peaks ~0.009, under the 0.01
    speech threshold) needs a boost or nothing registers as speech; loud mics leave this at 1."""
    try:
        return float(MIC_GAIN.read_text(encoding="utf-8").strip()) if MIC_GAIN.exists() else 1.0
    except ValueError:
        return 1.0


def _vocab_terms():
    """The terms Parakeet is biased toward, so it stops hearing "Notecraft" as "high ideas". Two
    sources: his project folder names (scanned off ~/workspace, plus any roots in vocab-roots.txt),
    and his hand-kept lexicon.md - coined names and the domain vocabulary of his fields alike, the
    same file the brain carries as standing context, so a term added in one place fixes both."""
    from entity.vocabulary import scan_terms

    roots = [WORKSPACE]
    if VOCAB_ROOTS.exists():
        roots += [Path(line) for line in VOCAB_ROOTS.read_text(encoding="utf-8").splitlines() if line.strip()]
    return scan_terms(roots) | set(lexicon_terms(load_lexicon()))


def _agent_inbox_note(inbox):
    """Persona line telling the Entity how its agents reach the user - the exact absolute path, since
    the agents run in other projects' worktrees and can't guess where the Entity keeps its inbox."""
    return (
        " When you put a background agent on a task, tell that agent - in its own instructions - to "
        f"write anything it needs from the user (a question, or that it's ready for review) as a single "
        f"line to {inbox}\\<a-short-agent-name>.txt. the user can't watch the agents' screens, so that "
        "inbox is the only way he hears from them - always set it up when you delegate."
    )


def _build_ears(text_mode, stop, interrupt):
    """Return (stt, mic, recorder) — mic/recorder are None in text mode; both close on exit.
    `interrupt` lets a quiet moment be broken off so the Entity can pass on queued agent news."""
    if text_mode:
        return ConsoleSTT(), None, None
    from datetime import datetime

    import sounddevice as sd

    from entity.mic import BackgroundMicrophone, Microphone, choose_input_device, probe_input_device
    from entity.recorder import AudioRecorder
    from entity.stt_mic import MicSTT
    from entity.transcribe import CorrectingTranscriber, ParakeetTranscriber

    # Bias transcription toward his own vocabulary so "Notecraft" stops coming back as "high ideas".
    terms = _vocab_terms()
    if terms:
        print(f"(custom vocabulary: {len(terms)} of your terms, e.g. {', '.join(sorted(terms)[:3])})")
    transcriber = CorrectingTranscriber(ParakeetTranscriber(), terms)
    transcriber.warmup()  # load the 2.4 GB model now, not on the first spoken turn

    # Don't trust the OS default input - on this machine it's a dead VR-headset mic. Pick the input
    # that's actually hearing the room (or an override the user drops in mic.txt), staying on the
    # default's host API so the stream can actually be opened, and say which mic won.
    override = MIC_OVERRIDE.read_text(encoding="utf-8").strip() if MIC_OVERRIDE.exists() else None
    default_input = sd.default.device[0]
    hostapi = sd.query_devices(default_input)["hostapi"] if default_input is not None else None
    device, device_name = choose_input_device(
        sd.query_devices(), probe_input_device, override=override, hostapi=hostapi
    )
    gain = _mic_gain()
    print(f"(listening on mic: {device_name or 'system default'}{f', gain x{gain:g}' if gain != 1.0 else ''})")
    # Capture on a background thread: keep draining the mic even while Parakeet is transcribing, so
    # nothing he says mid-transcription is lost to a PortAudio overflow.
    mic = BackgroundMicrophone(Microphone(device=device, gain=gain))
    recorder = AudioRecorder(RUNTIME_DIR / "audio" / f"session-{datetime.now():%Y%m%d-%H%M%S}.wav")
    print(f"(saving your audio to {recorder.path} - nothing you say gets lost, even on a crash)")
    cue = lambda: print("  ✓ got it", flush=True)  # visual "registered" the instant you say "over"
    stt = MicSTT(transcriber, mic, stop=stop, cue=cue, recorder=recorder, interrupt=interrupt)
    return stt, mic, recorder


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    text_mode = "--text" in argv
    muted = "--mute" in argv
    timings = "--no-timings" not in argv  # per-turn think/speak readout is on unless he opts out

    # Shutdown is a spoken/typed farewell ("goodbye entity", "quit") or Ctrl-C. Enter is NOT quit -
    # it's the barge-in: press it to cut off whatever the Entity is saying (he had a 15-minute
    # ramble he couldn't stop). Each Enter sets `barge_in`; the Conversation clears it per turn.
    stop = threading.Event()
    barge_in = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    # Word from the agents the Entity drives lands in this inbox; the watcher tails it and the
    # Entity speaks each new line at the next lull (never cutting the user off).
    AGENT_INBOX.mkdir(parents=True, exist_ok=True)
    outbox = Outbox()
    # Don't just wait to be told - watch the agents. If one goes silent past the threshold, the
    # monitor surfaces a heads-up so the user isn't left in the dark by a hung or stalled agent.
    quiet_monitor = QuietMonitor(outbox, quiet_after=AGENT_QUIET_AFTER)
    inbox_watcher = InboxWatcher(AGENT_INBOX, outbox, monitor=quiet_monitor)
    inbox_watcher.start()

    print("Entity is waking up...")
    persona = (
        compose_persona(DEFAULT_PERSONA, load_profile(), load_learned(), load_lexicon())
        + _agent_inbox_note(AGENT_INBOX)
        + _fresh_worktree_note()
    )
    sdk_brain = SdkBrain(persona=persona)
    sdk_brain.warmup()
    # Heartbeat: on a quiet timer, ask the brain if any agent has news he doesn't have yet and queue
    # it to the Outbox, so word from an agent reaches him the moment it lands, not only when he asks.
    heartbeat = HeartbeatMonitor(sdk_brain, outbox)
    heartbeat.start()
    stt, mic, recorder = _build_ears(text_mode, stop, outbox.arrived)

    tts = NullTTS() if muted else SystemTTS(rate=2)

    # Driving a fleet is just something you ask the Entity to do in conversation: this wrapper
    # catches a "[SUPERVISE] ..." directive from the brain and runs the agents through the same voice.
    # Each session gets a fresh timestamped transcript; a worktree it names but that doesn't exist yet
    # is cut fresh from current origin/main (supervise's default) before the agent starts.
    fleet_io = ConsoleFleetIO() if text_mode else VoiceFleetIO(speak=tts.speak, listen=stt.listen)
    brain = SupervisingBrain(sdk_brain, fleet_io, make_log=_make_fleet_log)

    def watch_keys():
        for _ in sys.stdin:  # every Enter is a barge-in: shut the current reply up
            barge_in.set()

    if not text_mode:
        threading.Thread(target=watch_keys, daemon=True).start()

    if text_mode:
        print("Entity is here. Type to talk; say 'quit' or 'goodbye entity' to end.")
    else:
        print("Entity is here. Speak, and say 'over' when you finish each turn.")
        print("Press Enter to cut it off. To quit, say 'goodbye entity over' (or Ctrl-C).")
    if muted:
        print("(muted: replies are shown, not spoken)")
    print()

    if not text_mode and not muted:
        tts.speak("I'm ready. What's on your mind?")  # say out loud that startup finished

    had_conversation = []
    farewelled = []

    def show(turn):  # the terminal transcript itself is the Console's job now; this is just bookkeeping
        had_conversation.append(True)
        if turn.farewell:
            farewelled.append(True)  # the goodbye was already said this turn; don't repeat it below

    # A beat to read a reply before the mic reopens, but not in text mode (he sets his own pace there).
    read_pause = 0.0 if text_mode else 1.2
    console = Console(voice=not text_mode)

    try:
        Conversation(
            stt, brain, tts, outbox=outbox, interrupt=barge_in, wake=outbox.arrived,
            console=console, read_pause=read_pause, timings=timings,
        ).run(should_continue=lambda: not stop.is_set(), on_turn=show)
    except KeyboardInterrupt:
        stop.set()
    finally:
        inbox_watcher.stop()
        heartbeat.stop()
        if not farewelled:  # one goodbye: a spoken farewell already said it; only cover Ctrl-C/stop here
            if not text_mode and not muted:
                try:
                    tts.speak("Be seeing you.")
                except Exception:
                    pass
            print("Be seeing you.")
        if had_conversation:  # remember what it learned - bounded so a slow model can't hang the exit
            try:
                append_learned(consolidate(brain))
            except Exception:
                pass
        for closer in (
            brain.close,
            mic.close if mic is not None else None,
            recorder.close if recorder is not None else None,
        ):
            try:
                if closer is not None:
                    closer()
            except Exception:
                pass


if __name__ == "__main__":
    main()
