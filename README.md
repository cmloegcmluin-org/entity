<img src="assets/excephalon.png" alt="the Chaosphere: a brain in a spiked wire cage" width="128" align="right">

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
Replies are spoken **as they are written**, sentence by sentence — first words in a couple of
seconds, one stop silencing everything still queued — with no stock phrases around them: no
acknowledgement line, no "I'll get back to you on that", no length gate.

**The ear stays open** (window mode). While Entity is only *thinking*, nothing is coming out of
the speakers, so whatever you say lands in your draft as usual. While its voice is *sounding*,
what you say is judged against the words it is speaking: its own voice arriving back through the
mic is dropped, your words are kept in the draft — talking over it doesn't mean being unheard —
and a barked stop word cuts the audio, while a sentence from the TV never can.

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

- It runs the **fast model tier** (Haiku), because talking is a fast job: its work is to answer,
  decide, and pull levers — never to investigate. `tools=[]` strips every built-in tool, so
  nothing it does mid-turn can take longer than a breath; what it knows about the fleet arrives
  as text in the turn (the desk's briefing), so "how's it going" is answered in the breath it
  was asked.
- `setting_sources=[]` loads **none** of your user/project/local settings, so the Entity never
  inherits your global coding `CLAUDE.md` or hooks. (Loading them made it answer in quoted-block
  reply format and fire that format's Stop hook every turn, which exploded latency to ~50s.)
- Acting goes through **typed in-process tools** (`entity.actions`) — `start_agent`, `tell_agent`,
  `set_next_agent_model`, `file_improvement`, `update_persona`, `close_agent_tab`, … — with
  `permission_mode="bypassPermissions"`, because a spoken conversation has no terminal to approve
  in. The coding agents it dispatches are the opposite: those run approval-gated
  (`entity.supervised_agent`) on Opus-tier models.
- Replies **stream**: each text delta reaches the voice as the model writes it, so the first
  sentence is sounding while the rest is still forming.
- One session threads every turn, so it remembers what you just said. Once the conversation has
  grown past a token budget it **compacts** — a fresh session reseeded with the last few turns
  verbatim, so turns stay fast however long you talk.
- Runs on the **Claude Max subscription** — OAuth is read independently of settings, so no API key.

The SDK is async; `SdkBrain` runs it on a private background event loop so the rest of the app
keeps a plain synchronous `respond(text) -> text`. A startup warmup absorbs the worst first-turn
cold start.

## Agents

Driving Claude Code agents is not a mode — it's something you ask for in conversation. The brain
calls a typed tool (`entity.actions`, an in-process MCP server wired to the desk), and what you
hear is its own sentence about what it set in motion — never a control phrase:

- `start_agent(path, task)` — a fresh agent in that worktree (a new path is cut from
  `origin/main` first), with your requirements as its task.
- `tell_agent(name, message)` — say something more to an agent already running; an undeliverable
  message comes back as a failure it has to tell you about, never a claimed delivery.
- `file_improvement(item)` — file an enhancement into your profile, visible in the window at once.
- `update_persona(instruction)` / `remember(fact)` — Entity editing itself: a standing change to
  how it behaves lands in its persona overlay, a durable fact in what it has learned. Both take
  hold next start, and both are editable by hand on the Persona and Memory pages.
- `mark_ready(name, steps)` / `record_verdict(name, verdict, feedback)` — the delivery loop as
  code: presented work is recorded with its see-it-running steps, and a verdict can only be
  recorded on work that was presented. Approval sends the agent to land it and — once it reports
  the merge — wraps it up; rejection carries your feedback straight back.
- `ask_foreman(name, question)` — a stuck agent goes to the foreman: a smarter model (one
  persistent Opus session, paid for per snag, not per turn) that reads the agent's task and log
  tail, answers its technical questions or pushes it to finish, and bothers you only with what is
  genuinely yours — preference, scope, sign-off.
- `set_next_agent_model(choice)` / `close_agent_tab(name)` — the other levers, same shape.

Each agent is a live session the desk can always reach, its roster is a file that survives a
context reset, and the fleet itself survives a restart: the desk records every agent's CLI
session in `runtime/agents.json` — including where each piece of work stands in the loop — and on
startup it resumes each one; an agent caught mid-task
is told to pick up where it left off. Once you've signed off on an agent's work, Entity wraps it
up on its own: the log is archived, the session ends, the worktree is removed. The whole exchange
is written to `runtime/agent-logs/<name>.log` — which the window tails as its own tab. Not just what the agent says: every command it runs and what came
back, every edit and its diff, with a failure marked as one, so what an agent did can be read
rather than taken on trust. Agents reach you by writing a line into `runtime/agent-inbox/`; the
Entity speaks it at the next lull, and flags an agent that has gone quiet for too long. When
several are ready at once it reads out their names, numbered, and waits — say a number or any word
of a name and you get that one, then what is still waiting.

## Architecture

Three swappable adapters behind small interfaces, tied together by one orchestrator:

- `SpeechToText.listen() -> str` — `Dictation` in the window, `MicSTT` ("over"-terminated) in a
  terminal, `ConsoleSTT` with `--text`.
- `Brain.respond(utterance, on_text=...) -> str` — `SdkBrain`, streaming its reply out as it is
  written, acting through `entity.actions`.
- `TextToSpeech` — `Speaker` (`entity.voice`) speaking Kokoro sentences as they form, with
  `SystemTTS` (Windows System.Speech via PowerShell) serving until the model is fetched and
  `NullTTS` when muted; `SwappableTTS` swaps the upgrade in mid-session.
- `Conversation` — the listen → think → speak loop, with farewell exit and error resilience.

Swapping any layer touches one adapter and nothing else. Speech-in is split two ways: `mic`
(hardware capture) and `transcribe` (local Parakeet via `onnx-asr`). The window is a mirror, not
a second implementation — everything that can be wrong lives outside it and is tested without a
display.

## Develop

```
.venv/Scripts/python -m pytest
```
