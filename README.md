# The Entity

A local, voice-in/voice-out, memory-persistent partner you *pair* with on your life. You talk;
it listens, thinks with Claude, and talks back — and it keeps a durable memory so it doesn't
lose the thread across days or months. It can also put Claude Code agents on real work for you
and tell you, in one sentence, when they need something.

Everything personal lives in a gitignored `runtime/` directory. Nothing about you is in this
source: the Entity learns your name, your context and your vocabulary from files you write.

## Run it

```
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"

.venv/Scripts/pythonw -m entity --gui   # the window (or double-click Entity.bat)
.venv/Scripts/python  -m entity         # speak to it in a terminal, hear spoken replies
.venv/Scripts/python  -m entity --text  # type instead of speaking
```

`--mute` shows replies without speaking them; `--no-timings` hides the per-turn think/speak
readout. `tools/install-start-menu.ps1` adds a Start Menu entry with the app's icon.

In the window the mic is a **state**, not a walkie-talkie: turn it on and everything you say is
transcribed into an editable draft, which you send with Submit. In a terminal, say **"over"** to
hand the turn back (silence detection was too flaky). To end, say or type **"goodbye entity"** or
**"quit"** — in voice mode that's "goodbye entity over" (or Ctrl-C).

**Cut it off** — while it's *speaking* or *thinking* — by pressing **Enter** or saying **"stop"**
("shut up" / "quiet" / "enough" / "wait" also work); it drops the reply and goes back to listening.
A slow request keeps working in the background instead of blocking, and in a terminal a long answer
is offered ("ready for it?") before it's spoken rather than dumped on you.

## What you put in `runtime/`

| File | What it is |
|---|---|
| `profile.md` | Your standing profile, in `## ` sections. Its `# ` title line is what the Entity calls you. |
| `learned.md` | Facts the Entity captured itself, appended at the end of each session. Yours to edit. |
| `lexicon.md` | Your working vocabulary — coined names, domain terms, the people you work with. |
| `lexicon-path.txt` | Optional: one line naming the lexicon file, if you keep it somewhere shared. |
| `mic.txt` | Optional: a device-name substring to force a specific microphone. |
| `mic-gain.txt` | Optional: a number to boost a quiet mic (e.g. `5`). |
| `vocab-roots.txt` | Optional: extra directories, one per line, whose folder names seed the vocabulary. |

The profile, the learned facts and the lexicon are folded into the brain's system prompt at
startup, so it knows you without being re-told; at the end of a session the brain is asked what
new durable facts came up, and those are appended to `learned.md`. The lexicon does triple duty:
standing context, transcription bias (see `vocabulary`), and — if you point `lexicon-path.txt` at
a shared copy — whatever else transcribes you.

## The brain, concretely

One persistent Claude session held open through the Agent SDK (`entity.brain_sdk.SdkBrain`).
Keeping a single warm session — instead of re-spawning the `claude` CLI every turn — is what
makes it feel live:

- `setting_sources=[]` loads **none** of your user/project/local settings, so the Entity never
  inherits your global coding `CLAUDE.md` or hooks. (Loading them made it answer in quoted-block
  reply format and fire that format's Stop hook every turn, which exploded latency to ~50s.)
- It keeps its native tools, with `permission_mode="bypassPermissions"`, because a spoken
  conversation has no terminal to approve in — a gated tool would simply hang. The coding agents
  it dispatches are the opposite: those run approval-gated (`entity.supervised_agent`).
- One session threads every turn, so it remembers what you just said. Once the conversation has
  grown past a token budget it **compacts** — a fresh session reseeded with the last few turns
  verbatim, so turns stay fast however long you talk.
- Runs on the **Claude Max subscription** — OAuth is read independently of settings, so no API key.

The SDK is async; `SdkBrain` runs it on a private background event loop so the rest of the app
keeps a plain synchronous `respond(text) -> text`. Per-turn latency is a few seconds and varies
with the server; a startup warmup absorbs the worst first-turn cold start.

## Agents

Driving Claude Code agents is not a mode — it's something you ask for in conversation. The brain
answers with a marker line, `entity.supervising_brain` acts on it, and you hear a short
confirmation instead of the marker:

- `[SUPERVISE] <worktree>` — start a fresh agent there, with your requirements as its task.
- `[TELL] <name>: <message>` — say something more to an agent already running.
- `[IMPROVE] <one line>` — file an enhancement into your profile, visible in the window at once.

Each agent is a live session the desk can always reach, its roster is a file that survives a
context reset, and the whole exchange is written to `runtime/agent-logs/<name>.log` — which the
window tails as its own tab. Not just what the agent says: every command it runs and what came
back, every edit and its diff, with a failure marked as one, so what an agent did can be read
rather than taken on trust. Agents reach you by writing a line into `runtime/agent-inbox/`; the
Entity speaks it at the next lull, and flags an agent that has gone quiet for too long.

## Architecture

Three swappable adapters behind small interfaces, tied together by one orchestrator:

- `SpeechToText.listen() -> str` — `Dictation` in the window, `MicSTT` ("over"-terminated) in a
  terminal, `ConsoleSTT` with `--text`.
- `Brain.respond(utterance) -> str` — `SdkBrain`, wrapped by `SupervisingBrain`.
- `TextToSpeech.speak(text)` — `SystemTTS` (Windows System.Speech via PowerShell, no package) or
  `NullTTS` when muted.
- `Conversation` — the listen → think → speak loop, with farewell exit and error resilience.

Swapping any layer touches one adapter and nothing else. Speech-in is split two ways: `mic`
(hardware capture) and `transcribe` (local Parakeet via `onnx-asr`). The window (`gui`) is a
mirror, not a second implementation — everything that can be wrong lives outside Tk and is tested
without a display.

## Develop

```
.venv/Scripts/python -m pytest
```
