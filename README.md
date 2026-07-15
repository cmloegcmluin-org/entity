# The Entity

A local, voice-in/voice-out, memory-persistent partner you *pair* with on your life. You talk;
it listens, thinks with Claude, and talks back — and it keeps a durable memory so it doesn't
lose the thread across days or months.

This is **category #1, Entity construction**. Everything else (fleet control, integrations,
better voice, the life-goal work itself) is deliberately out of scope here.

## The five construction subtasks

| | Subtask | v1 choice |
|---|---|---|
| a | **Speech-to-text** | local Parakeet (`onnx-asr`); talk and say "over" to end your turn (`--text` to type) |
| b | **Text-to-speech** | Windows System.Speech via PowerShell — cheapest robot voice, zero deps; Piper/Cartesia later |
| c | **Context management** | durable markdown facts + SQLite log + small always-loaded index + scheduled consolidation |
| d | **Brain** | one persistent Claude session (Agent SDK) on the Max plan — ~1.7s/turn, no API bill |
| e | **Preloading** | seed the (c) memory layer with life-context once (c) exists |

## The brain, concretely

The brain is **one persistent Claude session** held open through the Agent SDK
(`entity.brain_sdk.SdkBrain`). Keeping a single warm session — instead of re-spawning the
`claude` CLI every turn — is what makes it feel live:

- `allowed_tools=[]` strips the coding-agent tool suite, so the context stays small.
- `setting_sources=[]` loads **none** of your user/project/local settings — so the Entity never
  inherits your global coding `CLAUDE.md` or hooks. (Loading them made it answer in `>>`/`>`
  quote blocks and fire the terminal's Stop hook every turn, which exploded latency to ~50s.)
- One session threads every turn, so it remembers what you just said.
- Runs on the **Claude Max subscription** — OAuth is read independently of settings, so no API key.

The SDK is async; `SdkBrain` runs it on a private background event loop so the rest of the app
keeps a plain synchronous `respond(text) -> text`. Per-turn latency is a few seconds and varies
with the server; a startup warmup absorbs the worst first-turn cold start.

## Architecture

Three swappable adapters behind small interfaces, tied together by one orchestrator:

- `SpeechToText.listen() -> str` — `MicSTT` (Parakeet, "over"-terminated) by default; `ConsoleSTT` with `--text`.
- `Brain.respond(utterance) -> str` — `SdkBrain`.
- `TextToSpeech.speak(text)` — `SystemTTS` (or `NullTTS` when muted).
- `Conversation` — the listen → think → speak loop, with farewell exit and error resilience.

Swapping any layer touches one adapter and nothing else. Speech-in is split two ways: `mic`
(hardware capture) and `transcribe` (Parakeet); `MicSTT` ends a turn on the spoken word "over".

## Run it

```
~/workspace/entity/.venv/Scripts/python -m entity           # speak to it, hear spoken replies
~/workspace/entity/.venv/Scripts/python -m entity --text    # type instead of speaking
~/workspace/entity/.venv/Scripts/python -m entity --timings # show per-turn think/speak seconds
```

Speak, then say **"over"** to hand the turn back (silence-detection was too flaky). To end,
say (or type) **"goodbye entity"** or **"quit"** — in voice mode that's "goodbye entity over".
Enter and Ctrl-C also try to quit but are unreliable under Git Bash's terminal.

## Develop

```
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest
```
