# The Entity

A local, voice-in/voice-out, memory-persistent partner you *pair* with on your life. You talk;
it listens, thinks with Claude, and talks back — and it keeps a durable memory so it doesn't
lose the thread across days or months.

This is **category #1, Entity construction**. Everything else (fleet control, integrations,
better voice, the life-goal work itself) is deliberately out of scope here.

## The five construction subtasks

| | Subtask | v1 choice |
|---|---|---|
| a | **Speech-to-text** | local Parakeet (`onnx-asr`), shares Notecraft' model cache; VAD-gated turns (next increment) |
| b | **Text-to-speech** | Windows System.Speech via PowerShell — cheapest robot voice, zero deps; Piper/Cartesia later |
| c | **Context management** | durable markdown facts + SQLite log + small always-loaded index + scheduled consolidation |
| d | **Brain** | one persistent Claude session (Agent SDK) on the Max plan — ~1.7s/turn, no API bill |
| e | **Preloading** | seed the (c) memory layer with life-context once (c) exists |

## The brain, concretely

The brain is **one persistent Claude session** held open through the Agent SDK
(`entity.brain_sdk.SdkBrain`). Keeping a single warm session — instead of re-spawning the
`claude` CLI every turn — is what makes it feel live:

- `allowed_tools=[]` strips the coding-agent tool suite, so the context stays small.
- `setting_sources=["project", "local"]` (no `user`) keeps the Max OAuth login but drops the
  global coding instructions, so the Entity talks like a companion, not a code reviewer.
- One session threads every turn, so it remembers what you just said.
- Runs on the **Claude Max subscription** — no separate API key or per-token bill.

Measured: ~0.9s to connect at startup, then **~1.7–2.5s per turn** (versus ~5s when the CLI
was re-spawned each turn). The SDK is async; `SdkBrain` runs it on a private background event
loop so the rest of the app keeps a plain synchronous `respond(text) -> text`.

## Architecture

Three swappable adapters behind small interfaces, tied together by one orchestrator:

- `SpeechToText.listen() -> str` — `ConsoleSTT` today (typed); Parakeet mic next.
- `Brain.respond(utterance) -> str` — `SdkBrain`.
- `TextToSpeech.speak(text)` — `SystemTTS` (or `NullTTS` when muted).
- `Conversation` — the listen → think → speak loop, with farewell exit and error resilience.

Swapping any layer touches one adapter and nothing else.

## Run it

```
~/workspace/entity/.venv/Scripts/python -m entity          # talk by typing, hear spoken replies
~/workspace/entity/.venv/Scripts/python -m entity --mute   # text only
~/workspace/entity/.venv/Scripts/python -m entity --timings # show per-turn think/speak seconds
```

Say "goodbye entity" or press Ctrl-D to end. (In Git Bash's default terminal, prefix with
`winpty` so the typing prompt shows.)

## Develop

```
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest
```
