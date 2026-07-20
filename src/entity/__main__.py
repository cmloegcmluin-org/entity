"""Run the Entity: `python -m entity` (speak to it), or double-click Entity.bat for the window.

  --gui         a window instead of the terminal: live transcript + a STOP button
  --text        type instead of speaking
  --mute        show replies as text, don't speak them
  --no-timings  hide the per-turn think/speak readout (shown by default)
"""

import signal
import sys
import threading
from datetime import datetime
from pathlib import Path

from entity.agent_desk import AgentDesk
from entity.brain_sdk import DEFAULT_PERSONA, SdkBrain
from entity.console import Console
from entity.conversation import DEFAULT_LONG_ANSWER_CHARS, Conversation
from entity.inbox_watcher import InboxWatcher, QuietMonitor
from entity.mirror import TranscriptFeed
from entity.memory import (
    append_learned,
    compose_persona,
    lexicon_terms,
    load_learned,
    load_lexicon,
    load_profile,
    user_name,
)
from entity.outbox import Outbox
from entity.shutdown import consolidate
from entity.stt_console import ConsoleSTT
from entity.supervising_brain import SupervisingBrain
from entity.transcript import Transcript, recent_turns
from entity.tts_system import NullTTS, SystemTTS

RUNTIME_DIR = Path(__file__).resolve().parents[2] / "runtime"
AGENT_INBOX = RUNTIME_DIR / "agent-inbox"  # agents drop questions/review-ready notes here, one per line
ACTIVE_AGENTS = RUNTIME_DIR / "active-agents.txt"  # who the Entity has running, readable after a reset
AGENT_LOGS = RUNTIME_DIR / "agent-logs"  # one timestamped exchange log per agent, written by the desk
TRANSCRIPTS = RUNTIME_DIR / "transcripts"  # one timestamped record per conversation, as it happens
MIC_OVERRIDE = RUNTIME_DIR / "mic.txt"  # optional: a device-name substring to force a specific mic
MIC_GAIN = RUNTIME_DIR / "mic-gain.txt"  # optional: a number to boost a quiet mic (e.g. 5)
VOCAB_ROOTS = RUNTIME_DIR / "vocab-roots.txt"  # optional: extra dirs (one per line) to mine for project names
WORKSPACE = Path.home() / "workspace"  # default project tree; its folder names seed the custom vocabulary
AGENT_QUIET_AFTER = 20 * 60  # seconds of silence from an agent before the Entity flags it to the user


def _fresh_worktree_note():
    """Persona line: new work means a new worktree cut from freshly-fetched origin/main, not a stale
    local resume, because almost every request that arrives here is new work."""
    return (
        " Almost every agent you start is NEW work, which means a NEW worktree - don't resume an old "
        "one unless you are explicitly told to. When you set one up, base it on current "
        "origin/main: git fetch origin main first, then cut the worktree's branch from origin/main, "
        "so the agent never starts on stale local code that's fallen behind what's already merged."
    )


def _mic_gain():
    """How much to amplify the mic. A quiet input - an onboard mic can peak around 0.009, under
    the 0.01 speech threshold - needs a boost or nothing registers as speech; loud mics leave
    this at 1."""
    try:
        return float(MIC_GAIN.read_text(encoding="utf-8").strip()) if MIC_GAIN.exists() else 1.0
    except ValueError:
        return 1.0


def _vocab_terms():
    """The terms Parakeet is biased toward, so a coined name like "Notecraft" stops coming back as
    "note craft". Two sources: project folder names (scanned off the workspace root, plus any roots
    in vocab-roots.txt), and the hand-kept lexicon - coined names and domain vocabulary alike, the
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
        "write anything it needs from the user (a question, or that it's ready for review) as a single "
        f"line to {inbox}\\<a-short-agent-name>.txt. Nobody is watching the agents' screens, so that "
        "inbox is the only way they are heard from - always set it up when you delegate."
    )


def _agent_protocol_note(roster, logs):
    """Persona lines for the ONE way it should start and talk to coding agents.

    Without this the brain never emitted the directive at all: it fell back to spawning detached
    background agents with its own Agent tool, which hand back an id and nothing else - four in a
    row went unreachable, and its own context resets stranded the rest. The desk keeps each agent
    as a live session it can always reach, and the roster is a file, so a reset can't lose them.
    """
    return (
        " YOU ARE NOT THE ONLY THING THAT SPEAKS AS YOU. The app says a few lines in your name that "
        "you did not write, and they hear all of them as you: every turn of theirs is answered with "
        "'Got it.' before you have said anything at all. Others - a handoff line, an "
        "agent's notice, a confirmation - are reported to you afterwards in a system note. Own every "
        "one of them. Never tell them you have no record of saying something they heard you say; "
        "from where they sit that is you denying your own words, and it is the single thing that "
        "most makes you feel like several disconnected things wearing one name rather than someone "
        "they are talking to. "
        " ALWAYS ANSWER THEM AS WELL AS ACTING. Every directive below may be preceded by one short "
        "sentence in your own voice, and that sentence is what they hear - the marker never reaches "
        "them. Write it whenever they said anything that deserves a reply: they asked to be shown "
        "the work running and heard, in full, 'Passed that to <agent>.' Their question was answered "
        "and the answer thrown away. Say the sentence FIRST, then the marker on its own line. With "
        "no sentence they hear nothing beyond the 'Got it.' that already told them they were heard "
        "- which is exactly right when acting IS the whole reply, so add a sentence only when it "
        "carries something the ack does not. "
        " HOW TO PUT AN AGENT ON WORK - use this and nothing else. To start one, end your reply with "
        "the marker line `[SUPERVISE] <absolute path to the worktree>` followed, on the lines "
        "after it, by the task for the agent: the user's requirements passed on faithfully and "
        "completely (every constraint they stated - what to build, what counts as done, what NOT to "
        "do yet), plus the standing rules (agents report by inbox; don't merge without a sign-off). "
        "Emit the directive IMMEDIATELY - relaying a request needs NO investigation, no reading "
        "code, no tools first: the AGENT does the investigating, and every second you spend digging "
        "before dispatching is a second they wait for nothing. They hear your sentence, never the "
        "marker. To say something more to an agent that's already running - a correction, an "
        "answer, a follow-up question - end your reply with `[TELL] <agent name>: <your message>`. "
        f"The agents you have running are listed in {roster}; READ that file whenever you're unsure "
        "who's live, especially after any gap in your memory - it is the truth, and it survives you. "
        "Do NOT start coding agents with your own Agent/Task tool: those hand back an id you can "
        "never talk to again, and agents have been lost four at a time that way. Never wait on an "
        "agent either - starting one comes straight back, and whatever it says arrives on its own. "
        f"Every exchange with an agent is auto-written, timestamped, to {logs}\\<agent-name>.log - "
        "the window shows each of those as a live tab on its own, so you never need to open "
        "anything for a conversation to be watched. Never hand-write your own log of the exchange; "
        "the desk already keeps the real one. You CAN close an agent's tab when asked: move that "
        f"log into the 'closed' folder inside {logs} and the tab goes with it. Never say "
        "you're unable to. "
        "WHICH MODEL THE AGENTS RUN ON is his to choose and yours to report. They run on Opus 4.8 "
        "at high effort unless he says otherwise. When he names a model or an effort - 'use Fable "
        "on max', 'put them back on Opus', 'crank the effort up' - end your reply with `[MODEL] "
        "<what he said, verbatim>` and it takes effect for the NEXT agent you start; one already "
        "working keeps the model it opened with, so never tell him a running agent has changed. If "
        "he simply ASKS what they are on, that is a question, not a change: answer it. Never say "
        "you don't get to pick or that it's whatever the harness defaults to - you do get to pick, "
        "and being told that instead of an answer is what made him ask a second time. "
        "And when you are told to FILE a self-improvement, an enhancement, or an idea for "
        "your own roadmap, put `[IMPROVE] <the item, one line>` on a line of its own - it lands in "
        "the profile's Enhancements section and appears in the window immediately. File it the "
        "moment you are asked; never just promise to remember it. One line per item, and one "
        "`[IMPROVE]` line PER ITEM: asked for two tickets, two lines - filing one and leaving the "
        "other made them ask again for something they had already asked for."
    )


def _open_hearing(announce):
    """The hardware half of hearing - transcriber, mic, recorder - shared by both voice modes."""
    import sounddevice as sd

    from entity.mic import BackgroundMicrophone, Microphone, choose_input_device, probe_input_device
    from entity.recorder import AudioRecorder
    from entity.transcribe import CorrectingTranscriber, ParakeetTranscriber

    # Bias transcription toward the user's own vocabulary, so their coined names survive it.
    terms = _vocab_terms()
    if terms:
        announce(f"(custom vocabulary: {len(terms)} of your terms, e.g. {', '.join(sorted(terms)[:3])})")
    transcriber = CorrectingTranscriber(ParakeetTranscriber(), terms)
    transcriber.warmup()  # load the 2.4 GB model now, not on the first spoken turn

    # Don't trust the OS default input - it is often an idle headset or a virtual device that
    # hands back silence. Pick the input that's actually hearing the room (or an override the user
    # drops in mic.txt), staying on the default's host API so the stream can actually be opened,
    # and say which mic won.
    override = MIC_OVERRIDE.read_text(encoding="utf-8").strip() if MIC_OVERRIDE.exists() else None
    default_input = sd.default.device[0]
    hostapi = sd.query_devices(default_input)["hostapi"] if default_input is not None else None
    device, device_name = choose_input_device(
        sd.query_devices(), probe_input_device, override=override, hostapi=hostapi
    )
    gain = _mic_gain()
    announce(f"(listening on mic: {device_name or 'system default'}{f', gain x{gain:g}' if gain != 1.0 else ''})")
    # Capture on a background thread: keep draining the mic even while Parakeet is transcribing, so
    # nothing they say mid-transcription is lost to a PortAudio overflow.
    mic = BackgroundMicrophone(Microphone(device=device, gain=gain))
    recorder = AudioRecorder(RUNTIME_DIR / "audio" / f"session-{datetime.now():%Y%m%d-%H%M%S}.wav")
    announce(f"(saving your audio to {recorder.path} - nothing you say gets lost, even on a crash)")
    return transcriber, mic, recorder


def _persona():
    """Everything the Entity has been told about how to be - the standing rules, the user's own
    context, and every instruction added since. Composed in one place because the window shows this
    exact text, and a second copy would drift from the one the brain reads."""
    return (
        compose_persona(DEFAULT_PERSONA, load_profile(), load_learned(), load_lexicon())
        + _agent_inbox_note(AGENT_INBOX)
        + _fresh_worktree_note()
        + _agent_protocol_note(ACTIVE_AGENTS, AGENT_LOGS)
    )


def _build_ears(text_mode, stop, interrupt, announce=print):
    """Return (stt, mic, recorder) — mic/recorder are None in text mode; both close on exit.
    `interrupt` lets a quiet moment be broken off so the Entity can pass on queued agent news."""
    if text_mode:
        return ConsoleSTT(), None, None
    from entity.stt_mic import MicSTT

    transcriber, mic, recorder = _open_hearing(announce)
    cue = lambda: announce("  ✓ got it")  # visual "registered" the instant you say "over"
    stt = MicSTT(transcriber, mic, stop=stop, cue=cue, recorder=recorder, interrupt=interrupt)
    return stt, mic, recorder


def _session(*, announce, feed, gui, text_mode, muted, timings, stop, barge_in, attach=None):
    """Build everything and run the conversation to its end.

    Windowed, this runs on a worker while Tk owns the main thread - so the window is on screen
    within a moment of the click, and the model loading, the brain waking and the spoken greeting
    all happen where they can watch them. They were hearing "I'm ready" before any window appeared.
    """
    # Word from the agents the Entity drives lands in this inbox; the watcher tails it and the
    # Entity speaks each new line at the next lull (never cutting the user off).
    AGENT_INBOX.mkdir(parents=True, exist_ok=True)
    outbox = Outbox()
    # Don't just wait to be told - watch the agents. If one goes silent past the threshold, the
    # monitor surfaces a heads-up so the user isn't left in the dark by a hung or stalled agent.
    quiet_monitor = QuietMonitor(outbox, quiet_after=AGENT_QUIET_AFTER)
    inbox_watcher = InboxWatcher(AGENT_INBOX, outbox, monitor=quiet_monitor)
    inbox_watcher.start()

    announce("Entity is waking up...")
    # Seeded with the tail of the last session's transcript, so a restart - his only way of picking
    # up a fix - resumes the conversation instead of greeting him as a stranger.
    sdk_brain = SdkBrain(persona=_persona(), user=user_name(load_profile()),
                         seed_turns=recent_turns(TRANSCRIPTS))
    sdk_brain.warmup()
    # Driving agents is just something you ask the Entity to do in conversation: this wrapper catches
    # a "[SUPERVISE] ..." / "[TELL] ..." directive from the brain and hands it to the desk, which holds
    # each agent as a live session on its own thread. Starting or messaging an agent returns AT ONCE
    # and whatever it says comes back through the outbox, so agent work never blocks the conversation.
    desk = AgentDesk(outbox, roster_path=ACTIVE_AGENTS, log_dir=AGENT_LOGS, monitor=quiet_monitor)
    brain = SupervisingBrain(sdk_brain, desk)
    dictation = None
    if gui:
        # The window's mic is a STATE, not a walkie-talkie: continuous dictation into the editable
        # draft, controlled by voice ("hey entity" / "stop listening"), the mic button, and Submit.
        from entity.dictation import Dictation

        transcriber, mic, recorder = _open_hearing(announce)
        dictation = Dictation(
            transcriber, mic, recorder=recorder, stop=stop, interrupt=outbox.arrived,
            muted=True,  # the mic starts OFF; they turn it on when they're ready to talk
            on_draft=lambda t: feed.push("draft", t),
            on_state=lambda s: feed.push("state", s),
            on_level=lambda v: feed.push("level", v),
            on_submit_request=lambda: feed.push("submit", ""),
        )
        if attach is not None:
            attach(dictation)  # the window is already up, waiting to be wired to a mic
        dictation.start()
        stt = dictation
    else:
        stt, mic, recorder = _build_ears(text_mode, stop, outbox.arrived, announce)

    tts = NullTTS() if muted else SystemTTS(rate=2)

    def watch_keys():
        for _ in sys.stdin:  # every Enter is a barge-in: shut the current reply up
            barge_in.set()

    if not text_mode and not gui:  # the window binds Enter itself, and pythonw has no stdin
        threading.Thread(target=watch_keys, daemon=True).start()

    if text_mode:
        announce("Entity is here. Type to talk; say 'quit' or 'goodbye entity' to end.")
    elif gui:
        announce("Entity is here. Turn the mic on when you want to talk, or say 'hey Entity'.")
        announce("That same button stops it while it's speaking. Close the window to quit.")
    else:
        announce("Entity is here. Speak, and say 'over' when you finish each turn.")
        announce("Press Enter to cut it off. To quit, say 'goodbye entity over' (or Ctrl-C).")
    if muted:
        announce("(muted: replies are shown, not spoken)")
    announce()

    if not text_mode and not muted:
        # Guarded, because the mic is already live: unguarded, the greeting went out of their
        # speakers, back into the mic, and opened their draft box with "I do for you".
        if dictation is not None:
            dictation.begin_speaking()
        try:
            tts.speak("I'm ready. What can I do for you?")  # say out loud that startup finished
        finally:
            if dictation is not None:
                dictation.end_speaking()

    had_conversation = []
    farewelled = []

    def show(turn):  # the terminal transcript itself is the Console's job now; this is just bookkeeping
        had_conversation.append(True)
        if turn.farewell:
            farewelled.append(True)  # the goodbye was already said this turn; don't repeat it below

    # A beat to read a reply before the mic reopens, but not in text mode (they set their own pace there).
    read_pause = 0.0 if text_mode else 1.2
    # Keep the same lines the terminal shows, timestamped, so a session that went wrong can be read
    # back afterwards instead of the user having to copy their scrollback out by hand.
    session_record = Transcript(TRANSCRIPTS / f"session-{datetime.now():%Y%m%d-%H%M%S}.log")
    announce(f"(this conversation is being written to {session_record.path})\n")
    if gui:
        # The window renders a conversation, so it takes the Console's who-said-what seam rather
        # than its terminal lines - and no "(listening… say 'over')" notice, which is meaningless
        # next to a mic button and a level meter.
        console = Console(voice=True, record=session_record.write, listening_notice="",
                          echo=lambda t: None,
                          overwrite=lambda t: feed.push("overwrite", t),
                          messages=lambda role, text: feed.push("message", (role, text)))
    else:
        console = Console(voice=not text_mode, record=session_record.write)

    def converse():
        try:
            Conversation(
                stt, brain, tts, outbox=outbox, interrupt=barge_in, wake=outbox.arrived,
                console=console, read_pause=read_pause, timings=timings,
                # The window shows them every word, so nothing needs to be offered first.
                long_answer_chars=None if gui else DEFAULT_LONG_ANSWER_CHARS,
            ).run(should_continue=lambda: not stop.is_set(), on_turn=show)
        except KeyboardInterrupt:
            stop.set()
        finally:
            inbox_watcher.stop()
            desk.close()
            if not farewelled:  # one goodbye: a spoken farewell already said it; only cover Ctrl-C/stop here
                if not text_mode and not muted:
                    try:
                        tts.speak("Be seeing you.")
                    except Exception:
                        pass
                announce("Be seeing you.")
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

    converse()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    text_mode = "--text" in argv
    muted = "--mute" in argv
    timings = "--no-timings" not in argv  # per-turn think/speak readout is on unless they opt out
    gui = "--gui" in argv and not text_mode  # a window instead of the terminal (voice runs only)

    # In a windowed run every startup line goes to the window's feed INSTEAD of stdout - launched
    # from the Start Menu there is no terminal at all, and launched from a command line they don't
    # want the window's contents spat out there too.
    feed = TranscriptFeed() if gui else None

    def announce(line=""):
        if feed is not None:
            feed.push("line", line)
        else:
            print(line, flush=True)

    # Shutdown is a spoken/typed farewell ("goodbye entity", "quit") or Ctrl-C. Enter is NOT quit -
    # it's the barge-in: press it to cut off whatever the Entity is saying (they had a 15-minute
    # ramble they couldn't stop). Each Enter sets `barge_in`; the Conversation clears it per turn.
    stop = threading.Event()
    barge_in = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    running = dict(announce=announce, feed=feed, gui=gui, text_mode=text_mode, muted=muted,
                   timings=timings, stop=stop, barge_in=barge_in)
    if not gui:
        _session(**running)
        return

    # Windowed: the window opens FIRST and the whole session runs on a worker, so a click puts
    # something on screen at once instead of after a 2.4 GB model has loaded. Closing the window
    # asks the loop to stop (the mic checks `stop` every frame), and once the worker has wound all
    # the way down - goodbye said, memory consolidated - `done` lets the window end itself.
    import anyio

    from entity.chord import ChordListener, SubmitChord, foreground_is_ours
    from entity.desktop import open_window
    from entity.memory import DEFAULT_LEARNED_PATH, DEFAULT_PROFILE_PATH
    from entity.no_console import silence_child_consoles
    from entity.transcript import past_lines
    from entity.web import Mirror, create_app

    # With no console of its own to lend them, Windows gives each console child a new window: the
    # Claude CLI the brain runs was turning up as a second window on their desktop.
    silence_child_consoles(anyio)

    for line in past_lines(TRANSCRIPTS, current=None):
        feed.push("history", line)  # yesterday's sessions, above the divider - no more amnesia
    feed.push("line", "───────  this session  ───────")

    mirror = Mirror(feed)
    # The window is up before the model has loaded, so the mic does not exist yet; whatever is
    # pressed in that gap is dropped rather than raising at a page that cannot know.
    mic = {}

    app = create_app(
        mirror.model, mirror=mirror,
        on_submit=lambda text: mic.get("submit", lambda _: None)(text),
        on_stop=barge_in.set,
        on_mic=lambda recording: mic.get("set_recording", lambda _: None)(recording),
        on_auto_listen=lambda on: mic.get("set_auto_listen", lambda _: None)(on),
        profile_path=DEFAULT_PROFILE_PATH, learned_path=DEFAULT_LEARNED_PATH,
        persona=_persona(), agent_logs_dir=AGENT_LOGS,
    )
    # The modifier beside the spacebar + Enter submits the draft. It reaches no window on this
    # machine, so it arrives by keyboard hook instead - and only while the Entity is in front.
    ChordListener(SubmitChord(submit=lambda: feed.push("submit", ""),
                              focused=foreground_is_ours)).start()

    def worker():
        try:
            _session(attach=lambda d: mic.update(submit=d.submit,
                                                 set_recording=d.set_recording,
                                                 set_auto_listen=d.set_auto_listen), **running)
        finally:
            stop.set()

    threading.Thread(target=worker, daemon=True).start()
    open_window(app, icon=str(Path(__file__).resolve().parents[2] / "assets" / "entity.ico"))
    stop.set()  # the window was closed: ask the loop to wind down, as closing the Tk one did


if __name__ == "__main__":
    main()
