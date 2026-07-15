# The Entity

A local, voice-in/voice-out, memory-persistent partner you *pair* with on your life. You talk;
it listens, thinks with Claude, and talks back — and it keeps a durable memory so it doesn't
lose the thread across days or months.

This is **category #1, Entity construction**. Everything else (fleet control, integrations,
better voice, the life-goal work itself) is deliberately out of scope here.

## The five construction subtasks

| | Subtask | v1 choice |
|---|---|---|
| a | **Speech-to-text** | local Parakeet (`onnx-asr`), shares Notecraft' model cache; VAD-gated turns |
| b | **Text-to-speech** | `pyttsx3` (Windows SAPI) — cheapest local robot voice; Piper later |
| c | **Context management** | durable markdown facts + SQLite log + small always-loaded index + scheduled consolidation |
| d | **Brain** | Claude via the `claude` CLI on the Max plan (no extra API bill) |
| e | **Preloading** | seed the (c) memory layer with life-context once (c) exists |

## The brain, concretely

The Entity's brain is the local `claude` CLI run headless, once per turn:

```
claude -p --tools "" --setting-sources project,local \
  --system-prompt "<Entity persona>" --model sonnet \
  --output-format json  [--resume <session-id>]
```

- `--tools ""` strips the coding-agent tool suite → ~2s responses instead of ~15–60s.
- `--setting-sources project,local` (no `user`) keeps the Max OAuth login but drops the
  global coding instructions, so the Entity talks like a companion, not a code reviewer.
- `--resume` threads one session across turns for continuity.
- Runs on the **Claude Max subscription** — no separate API key or per-token bill.

## Architecture

Three swappable adapters behind small interfaces, tied together by one orchestrator:

- `SpeechToText.listen() -> str`
- `Brain.respond(utterance) -> str`
- `TextToSpeech.speak(text)`
- `Conversation` — the listen → think → speak loop.

Swapping any layer (e.g. Piper for SAPI, or a persistent-session brain) touches one adapter.

## Status

Under construction. See `tests/` for what is verified.

## Develop

```
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest
```
