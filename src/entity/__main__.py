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

from entity.actions import fleet_actions
from entity.agent_desk import AgentDesk
from entity.brain_sdk import DEFAULT_PERSONA, SdkBrain
from entity.console import Console
from entity.conversation import Conversation
from entity.errands import ErrandRunner
from entity.foreman import Foreman
from entity.inbox_watcher import InboxWatcher, QuietMonitor
from entity.mirror import TranscriptFeed
from entity.narrator import Narrator
from entity.memory import (
    DEFAULT_PROFILE_PATH,
    append_learned,
    complete_enhancement,
    compose_persona,
    lexicon_terms,
    load_learned,
    load_lexicon,
    load_persona_additions,
    load_profile,
    load_translations,
    number_enhancements,
    open_enhancements,
    translation_pairs,
    user_name,
)
from entity.outbox import Outbox
from entity.polish import Polisher
from entity.relay import notice
from entity.shutdown import consolidate
from entity.stt_console import ConsoleSTT
from entity.transcript import Transcript, recent_turns
from entity.tts_neural import KokoroEngine, ensure_voice, voice_choice
from entity.tts_system import NullTTS, SystemTTS
from entity.voice import Speaker, play_samples

RUNTIME_DIR = Path(__file__).resolve().parents[2] / "runtime"
AGENT_INBOX = RUNTIME_DIR / "agent-inbox"  # agents drop questions/review-ready notes here, one per line
ACTIVE_AGENTS = RUNTIME_DIR / "active-agents.txt"  # who the Entity has running, readable after a reset
AGENT_STATE = RUNTIME_DIR / "agents.json"  # the fleet's survival record: what a restart revives from
AGENT_LOGS = RUNTIME_DIR / "agent-logs"  # one timestamped exchange log per agent, written by the desk
TRANSCRIPTS = RUNTIME_DIR / "transcripts"  # one timestamped record per conversation, as it happens
MIC_OVERRIDE = RUNTIME_DIR / "mic.txt"  # optional: a device-name substring to force a specific mic
MIC_GAIN = RUNTIME_DIR / "mic-gain.txt"  # optional: a number to boost a quiet mic (e.g. 5)
VOCAB_ROOTS = RUNTIME_DIR / "vocab-roots.txt"  # optional: extra dirs (one per line) to mine for project names
WORKSPACE = Path.home() / "workspace"  # default project tree; its folder names seed the custom vocabulary
AGENT_QUIET_AFTER = 20 * 60  # seconds of silence from an agent before the Entity flags it to the user


def _fresh_worktree_note():
    """Persona line: new work means a new worktree, and naming a new path to start_agent is all it
    takes - the tool cuts it from freshly-fetched origin/main itself, so the brain never runs git."""
    return (
        " Almost every agent you start is NEW work, which means a NEW worktree - don't resume an "
        "old one unless you are explicitly told to. Name a fresh worktree path to start_agent (a "
        "short kebab-case name for the work, under the project's .claude/worktrees/) and the tool "
        "cuts it from current origin/main itself."
    )


def _projects_note():
    """Persona line: where the user's projects live, so the brain never has to ask. It asked for
    the path to a repo whose name alone identified it; the directory listings already knew."""
    from entity.worktrees import projects

    homes = [(root, projects(root)) for root in _project_roots()]
    homes = [(root, known) for root, known in homes if known]
    if not homes:
        return ""
    listed = "; ".join(f"{root}: {', '.join(known)}" for root, known in homes)
    return (
        f" Their projects live one directory per project under these roots - {listed}. When they "
        "name one, that is the repo - never ask where it is. A new agent for a project works in "
        "<that project's directory>\\.claude\\worktrees\\<short-task-name>."
    )


def _mic_gain():
    """How much to amplify the mic. A quiet input - an onboard mic can peak around 0.009, under
    the 0.01 speech threshold - needs a boost or nothing registers as speech; loud mics leave
    this at 1."""
    try:
        return float(MIC_GAIN.read_text(encoding="utf-8").strip()) if MIC_GAIN.exists() else 1.0
    except ValueError:
        return 1.0


def _project_roots():
    """Everywhere the user's projects live: the workspace, plus each root listed in
    vocab-roots.txt - one file feeding both the transcription vocabulary and the brain's map,
    so a root added there fixes "it can't hear the name" and "it asked where the repo is" at once."""
    roots = [WORKSPACE]
    if VOCAB_ROOTS.exists():
        roots += [Path(line.strip()) for line in VOCAB_ROOTS.read_text(encoding="utf-8").splitlines()
                  if line.strip()]
    return roots


def _vocab_terms():
    """The terms Parakeet is biased toward, so a coined name like "Notecraft" stops coming back as
    "note craft". Two sources: project folder names (scanned off every project root), and the
    hand-kept lexicon - coined names and domain vocabulary alike, the same file the brain carries
    as standing context, so a term added in one place fixes both."""
    from entity.vocabulary import scan_terms

    return scan_terms(_project_roots()) | set(lexicon_terms(load_lexicon()))


def _agent_inbox_note(inbox):
    """Persona line telling the Entity how its agents reach the user - the exact absolute path, since
    the agents run in other projects' worktrees and can't guess where the Entity keeps its inbox."""
    return (
        " When you put a background agent on a task, tell that agent - in its own instructions - to "
        "write anything it needs from the user (a question, or that it's ready for review) as a single "
        f"line to {inbox}\\<a-short-agent-name>.txt. Nobody is watching the agents' screens, so that "
        "inbox is the only way they are heard from - always set it up when you delegate."
    )


def _window_note(logs):
    """Persona lines about the window the user is looking at - the part of the world the brain
    can't see but keeps getting asked about."""
    return (
        f" Every exchange with an agent is auto-written, timestamped, to {logs}\\<agent-name>.log "
        "- the window shows each of those as a live tab of its own, so a conversation is already "
        "watchable and you never open anything for them. Never hand-write your own log of an "
        "exchange; the desk keeps the real one. Their agents run on Opus 4.8 at high effort "
        "unless they choose otherwise - the fleet briefing says what a fresh agent starts on, so "
        "when they ask, answer from it; never say the choice isn't yours to make. When they ask "
        "you to file an enhancement, file EVERY item they named - one file_improvement call per "
        "item; filing one of two made them ask again for something they had already asked for."
    )


def _open_ears(announce):
    """The hardware half of listening - transcriber, mic, recorder - shared by both voice modes.

    Not "hearing", which is `entity.hearing`: that module is the live line, and one name for both
    would have the next reader looking for a screen in the microphone code."""
    import sounddevice as sd

    from entity.mic import BackgroundMicrophone, Microphone, choose_input_device, probe_input_device
    from entity.recorder import AudioRecorder
    from entity.transcribe import CorrectingTranscriber, ParakeetTranscriber

    # Bias transcription toward the user's own vocabulary, so their coined names survive it, and
    # swap outright the phrases that come back as ordinary English ("cloud agent"). The window's
    # Translations page shows both lists, so nothing here is applied unseen.
    terms = _vocab_terms()
    if terms:
        announce(f"(custom vocabulary: {len(terms)} of your terms, e.g. {', '.join(sorted(terms)[:3])})")
    transcriber = CorrectingTranscriber(ParakeetTranscriber(), terms,
                                        translations=translation_pairs(load_translations()))
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
    context, and every instruction added since (its own persona overlay). Composed in one place
    because the window shows this exact text, and a second copy would drift from the one the brain
    reads."""
    return (
        compose_persona(DEFAULT_PERSONA, load_profile(), load_learned(), load_lexicon(),
                        additions=load_persona_additions())
        + _agent_inbox_note(AGENT_INBOX)
        + _fresh_worktree_note()
        + _projects_note()
        + _window_note(AGENT_LOGS)
    )


def _voice(announce):
    """The voice, fully loaded BEFORE the Entity says it is ready.

    It first shipped the other way - the robot System.Speech voice served while the neural model
    loaded in the background - and the first reply of every session came out robot-voiced. His
    call: "Just don't be ready until it loads. Time to start up is not precious; it's only time
    to respond while in session that matters." So startup blocks on the fetch (once ever) and the
    load (~2s), and the robot voice remains only for a machine where the neural one genuinely
    can't be had - said out loud, not discovered by ear."""
    paths = ensure_voice(RUNTIME_DIR / "tts", announce=announce)
    if paths is None:
        announce("(couldn't fetch the neural voice - the system voice will serve)")
        return SystemTTS(rate=2)
    name, speed = voice_choice(RUNTIME_DIR / "tts")
    announce(f"(loading the voice: {name} - change it in runtime/tts/voice.txt)")
    engine = KokoroEngine(*paths, voice=name, speed=speed)
    try:
        engine.say("Ready.")  # the load and the warm-up, paid here rather than mid-conversation
    except Exception as exc:
        announce(f"(the neural voice failed to load: {exc!r} - the system voice will serve)")
        return SystemTTS(rate=2)
    return Speaker(engine, play=play_samples)


def _build_ears(text_mode, stop, interrupt, announce=print):
    """Return (stt, mic, recorder) — mic/recorder are None in text mode; both close on exit.
    `interrupt` lets a quiet moment be broken off so the Entity can pass on queued agent news."""
    if text_mode:
        return ConsoleSTT(), None, None
    from entity.stt_mic import MicSTT

    transcriber, mic, recorder = _open_ears(announce)
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

    # Every agent event - finished, died, wrote to its inbox, gone quiet - takes one trip through
    # the brain so what the user hears is the brain's own sentence, not a label read aloud. The
    # narrator needs the brain, which doesn't exist yet; until it does (a few seconds of startup),
    # the capped plain notice still carries any news, because news must never wait on wiring.
    newsroom = {}

    def agent_events(kind, agent, report):
        narrator = newsroom.get("narrator")
        if narrator is not None:
            narrator.tell(kind, agent, report)
        else:
            outbox.push(notice(agent, report), about=agent)

    # Don't just wait to be told - watch the agents. If one goes silent past the threshold, the
    # monitor surfaces a heads-up so the user isn't left in the dark by a hung or stalled agent.
    quiet_monitor = QuietMonitor(outbox, quiet_after=AGENT_QUIET_AFTER, events=agent_events)
    inbox_watcher = InboxWatcher(AGENT_INBOX, outbox, monitor=quiet_monitor, events=agent_events)
    inbox_watcher.start()

    announce("Entity is waking up...")
    # The punctuation repairman: one small warm session that fixes pause-chopped sentence breaks
    # in a submitted draft, inside a hard deadline, changing no words (see entity.polish).
    polisher = Polisher()
    polisher.warmup()
    # The desk holds each agent as a live session on its own thread; the brain drives it through
    # typed in-process tools (start_agent, tell_agent, ...), so starting or messaging an agent
    # returns at once and whatever the agent says comes back through the outbox. Nothing the brain
    # does can block on agent work, and nothing it says doubles as a control channel.
    desk = AgentDesk(outbox, roster_path=ACTIVE_AGENTS, log_dir=AGENT_LOGS, monitor=quiet_monitor,
                     events=agent_events, state_path=AGENT_STATE,
                     # The machine-wide engineering law, split out of the user's personal config
                     # so working agents can be pointed at exactly it. Home-relative, so nothing
                     # personal enters the source and a machine without the split just skips it.
                     law_path=Path.home() / ".claude" / "engineering.md",
                     # Wrapping up an agent started for an Enhancements item ticks that item off
                     # the user's list (profile.md) - the pool they file into, self-draining as
                     # the work lands.
                     complete_enhancement=complete_enhancement)
    # The senior layer: engaged only when the brain hands it a stuck agent (ask_foreman), so its
    # bigger model is paid for per snag, never per turn.
    foreman = Foreman(desk, outbox)
    # The quiet errand hand: small local chores with no agent tab - "one agent per actual major
    # task", not one per little thing. Its outcomes take the news road like everything else.
    errands = ErrandRunner(RUNTIME_DIR, agent_events)
    actions_server, _ = fleet_actions(desk, foreman, errands)
    # Seeded with the tail of the last session's transcript, so a restart - their only way of picking
    # up a fix - resumes the conversation instead of greeting them as a stranger.
    brain = SdkBrain(persona=_persona(), user=user_name(load_profile()), actions=actions_server,
                     seed_turns=recent_turns(TRANSCRIPTS))
    brain.warmup()
    # From here on, news arrives in the brain's own voice - and worded by where the work stands:
    # a finished turn is presentation news while building, wrap-up news while landing approved work.
    newsroom["narrator"] = Narrator(brain, outbox, stage_of=desk.delivery_stage)
    # "I close it and reopen it constantly": bring back every agent the last process recorded,
    # each resumed on its old session - one caught mid-task is told to pick back up. After the
    # narrator, so an instantly-finishing revival is narrated, not read out as a label.
    revived = desk.revive()
    if revived:
        announce(f"(reattached to last session's agents: {', '.join(revived)})")
    dictation = None
    hearing = None
    if gui:
        # The window's mic is a STATE, not a walkie-talkie: continuous dictation into the editable
        # draft, controlled by voice ("hey entity" / "stop listening"), the mic button, and Submit.
        from entity.dictation import Dictation
        from entity.hearing import Hearing

        transcriber, mic, recorder = _open_ears(announce)
        # Words on screen while they are still saying them: the burst so far, read over and over on a
        # worker of its own. The same transcriber, on purpose - one 2.4 GB model, loaded already,
        # and onnxruntime will run it from both threads.
        hearing = Hearing(transcriber, lambda t: feed.push("hearing", t))
        hearing.start()
        dictation = Dictation(
            transcriber, mic, recorder=recorder, stop=stop, interrupt=outbox.arrived,
            hearing=hearing,
            polish=polisher.polish,  # pause-chopped punctuation repaired on the way to the brain
            muted=True,  # the mic starts OFF; they turn it on when they're ready to talk
            on_draft=lambda t: feed.push("draft", t),
            on_state=lambda s: feed.push("state", s),
            on_level=lambda v: feed.push("level", v),
            on_submit_request=lambda: feed.push("submit", ""),
            on_retract=lambda: feed.push("retract", ""),
        )
        if attach is not None:
            attach(dictation)  # the window is already up, waiting to be wired to a mic
        dictation.start()
        stt = dictation
    else:
        stt, mic, recorder = _build_ears(text_mode, stop, outbox.arrived, announce)

    tts = NullTTS() if muted else _voice(announce)

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
                          composing=lambda text: feed.push("composing", text),
                          messages=lambda role, text: feed.push("message", (role, text)))
    else:
        console = Console(voice=not text_mode, record=session_record.write)

    if not text_mode and not muted:
        # Spoken lines render as bubbles - "'I'm ready, what can I do for you?'... don't render
        # in the conversation view, but they should, because Entity says them aloud." Through the
        # console AFTER it exists, so the greeting is a message like any other. Still guarded,
        # because the mic is already live: unguarded, the greeting went out of their speakers,
        # back into the mic, and opened their draft box with "I do for you".
        greeting = "I'm ready. What can I do for you?"
        console.reply(greeting)
        if dictation is not None:
            dictation.begin_speaking()
        try:
            tts.speak(greeting)  # say out loud that startup finished
        finally:
            if dictation is not None:
                dictation.end_speaking()

    def converse():
        try:
            Conversation(
                stt, brain, tts, outbox=outbox, interrupt=barge_in,
                console=console, read_pause=read_pause, timings=timings,
                # The live truth about the fleet AND his Enhancements list, re-read from the file
                # every turn: the boot persona's copy of the list went stale and got DISBELIEVED
                # ("I can't see the Enhancements list"), while nothing carried in these per-turn
                # notes has ever faded or been denied.
                briefing=lambda: (
                    f"{desk.digest()}\nFresh agents start on {desk.running_on()}."
                    "\n\nHis Enhancements list - the OPEN items, live from the file this turn. "
                    "You CAN see this list: it is right here, always current, and it is the same "
                    "list his window's tab shows. You file to it with file_improvement, rewrite "
                    "an item by number with revise_enhancement, tick one DONE with "
                    "check_off_enhancement the moment its ask is finished, and agents you start "
                    "on an item tick it off themselves when their work lands:\n"
                    + (open_enhancements() or "(nothing open)")
                ),
            ).run(should_continue=lambda: not stop.is_set(), on_turn=show)
        except KeyboardInterrupt:
            stop.set()
        finally:
            inbox_watcher.stop()
            desk.close()
            if not farewelled:  # one goodbye: a spoken farewell already said it; only cover Ctrl-C/stop here
                console.reply("Be seeing you.")  # a spoken line renders as its bubble, and is recorded
                if not text_mode and not muted:
                    try:
                        tts.speak("Be seeing you.")
                    except Exception:
                        pass
            if had_conversation:  # remember what it learned - bounded so a slow model can't hang the exit
                try:
                    append_learned(consolidate(brain))
                except Exception:
                    pass
            for closer in (
                brain.close,
                foreman.close,
                errands.close,
                polisher.close,
                mic.close if mic is not None else None,
                recorder.close if recorder is not None else None,
                hearing.close if hearing is not None else None,
            ):
                try:
                    if closer is not None:
                        closer()
                except Exception:
                    pass

    converse()


def main(argv=None):
    # Give every enhancement a stable #id before anything reads the profile: the brain composes its
    # persona from this file and the window shows the same numbers, so "do twelve" points them both
    # at one task. Idempotent - it writes only the first time, when something is still unnumbered.
    number_enhancements(DEFAULT_PROFILE_PATH)
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
    from entity.memory import (
        DEFAULT_LEARNED_PATH,
        DEFAULT_PERSONA_ADDITIONS_PATH,
        DEFAULT_TRANSLATIONS_PATH,
    )
    from entity.no_console import silence_child_consoles
    from entity.transcript import past_lines
    from entity.mirror import Mirror
    from entity.web import create_app

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
        translations_path=DEFAULT_TRANSLATIONS_PATH,
        persona_additions_path=DEFAULT_PERSONA_ADDITIONS_PATH,
        # The same list the mic is about to be built with, so the page says what is in force
        # rather than what could be.
        terms=_vocab_terms(),
        persona=_persona(), agent_logs_dir=AGENT_LOGS,
    )
    # The modifier beside the spacebar + Enter submits the draft. It reaches no window on this
    # machine, so it arrives by keyboard hook instead - and only while the Entity is in front.
    # Held in a name for the app's lifetime, and asked whether it took: a hook that fails to
    # install is the one place this can die in silence, so it says so on screen rather than the
    # chord just quietly doing nothing.
    chord = ChordListener(SubmitChord(submit=lambda: feed.push("submit", ""),
                                      focused=foreground_is_ours))
    if not chord.start():
        feed.push("line", "(Win+Enter to submit is unavailable - the keyboard hook didn't install)")

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
