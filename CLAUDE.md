# Working on Entity

Read this before touching anything here. It is what previous sessions learned the expensive way —
mostly by shipping something that looked right and having the user discover, again, that it wasn't.

Entity is a voice-in/voice-out companion that also supervises Claude coding agents on its user's
behalf. The point is not convenience; it is presence — someone to show up for — and a single voice
that shields the user from the machinery underneath. Almost every law below exists because that
shield tore somewhere.

## Read the evidence. Never ask for it to be pasted.

Every session leaves artifacts. Use them before forming any theory:

| What | Where | Answers |
|---|---|---|
| What was on screen | `runtime/transcripts/session-*.log` | every printed and spoken line, timestamped |
| What the mic actually heard | `runtime/audio/session-*.wav` | whether a word reached the machine at all |
| What an agent said, as it said it | `runtime/agent-logs/<name>.log` | whether an agent is working or dead |
| Who is running right now | `runtime/active-agents.txt` | the roster, with last-heard times |
| What Entity knows about its user | `runtime/profile.md`, `runtime/learned.md` | standing context; both gitignored |

Asking the user to copy their scrollback is a defect in this project — the transcript exists
precisely so nobody ever has to. Reading the transcript is also how you check your own work:
several "fixed" claims were disproved by the transcript in the next message.

**Diagnose from the artifact, never from the code's intent.** The two most expensive wrong answers
in this project's history were both confident stories told without looking: an agent declared "dead"
that answered 43 seconds later, and a freeze blamed on a phrase rather than the latched flag that
caused it. If you cannot observe something, say so and ask for the one observation that would settle
it. A plausible reconciliation is worse than an admitted gap, because it gets acted on.

## The rules Entity lives by

These are user requirements, learned through failures somebody had to sit through. Persona text
enforces some; code enforces the rest, and where only the persona enforces something, treat that as
a known weakness rather than a solution.

- **Insulate the user from agents.** An agent's own words never reach them — not commit hashes, not
  test counts, not "I reran the suite myself". `relay.notice()` is the only door: agent name, first
  sentence, capped, and a pointer to its tab. Handed the raw stream, a person cannot tell whether
  they are talking to Entity or to the agent; the code, not the model, has to prevent it.
- **Brevity is the product.** A reply is cut at a sentence past ~260 characters, and the next turn
  tells the model it was cut. Long answers are not "delivered differently" — they are lost.
- **Never speak while the user's mic is on.** Unprompted speech and finished background answers both
  wait. It once broke in mid-sentence while someone was recording.
- **One handoff line, verbatim:** "I'll get back to you on that." after 5 seconds. No variations, no
  periodic progress updates — both were found worse than silence.
- **Never self-certify.** Green tests are not verification; the user's eyes are. Put the real thing
  in front of them, or give them the exact steps, and let them judge. And never present work for
  verification while a setup step of theirs is still outstanding.
- **When the user says something isn't there, it isn't.** They are looking at the screen; you are not.

## Failure patterns that have recurred here

- **A mechanism nobody perceives.** Truncation that the model never sees teaches it nothing; a layout
  "capped at half width" is worthless if the framework ignores the property. Before calling such a
  change done, name the recipient and state how the signal reaches them. Tests that assert the
  mechanism fired are not evidence anything received it. The chat bubbles took four attempts for
  exactly this reason — the wrap was measured and correct while the tint still painted edge to edge,
  and only screenshotting the pane and reading the pixels back showed it.
- **Latched flags.** `Outbox.arrived` is cleared only by draining. Any path that decides not to
  deliver must still drain, or the window's mic yields empty turns forever and submissions are
  never read. That froze a whole session.
- **Fan-out where one thing was named.** A worktree is recognized by its `.git`; globbing a directory
  once started an agent in `.venv`, `docs` and `src` of a single worktree.
- **Believing the model over the file.** Entity has claimed to have filed something, opened
  something, or verified something that had not happened. Check the artifact.

## Nothing personal in the source

The user's name, context, vocabulary and hardware are read at runtime from the gitignored
`runtime/` directory — never written into the source, so this repo can be public. `DEFAULT_PERSONA`
carries a `{user}` placeholder that `memory.compose_persona` fills from the title line of
`runtime/profile.md`; a checkout without one is addressed as "the user" and still reads as
sentences. Comments explain a decision by the behaviour it protects, not by whose behaviour it was.
Test fixtures use invented facts. When you add a comment here, write the failure, not the person.

## Shape of the code

`conversation.py` is the loop (listen → think → speak) and owns turn-taking, barge-in, and the
5-second handoff. `dictation.py` is the window's mic: a *state*, not a walkie-talkie — continuous
transcription into an editable draft, `hey entity` / `stop listening` to arm and disarm, `scratch
that` to take back what was just said, and it reports whether it is recording so nothing speaks
over the user. `hearing.py` is the live line: the burst so far, re-read on a worker of its own, with
a word shown only once two readings running have agreed on it — read its docstring before changing
any number in it, because every one was measured off real captured sessions. The window is a local web app, the
same shape as Notecraft: `mirror.py` is the conversation as a window shows it — the message model,
the thread-safe feed everything crosses on, and where each session starts — with no window in it,
so all of it is tested without a display; `web.py` serves it, `templates/` and `static/` are the
pages, and `desktop.py` puts them in an OS window of their own (Flask on a loopback port, pywebview
holding the view) rather than a browser tab. `links.py` decides what a message names that can be
opened, and opens it. `agent_desk.py` holds each agent as a live session in-process (handles
used to be lost to context resets) and streams the whole exchange into its log; `steps.py` decides
what a streamed message becomes there — the agent's words as messages, and its commands, diffs and
output as the machinery under them, capped at both ends with what was dropped counted in place.
`waiting.py` is what happens when several agents finish at once: they are read out numbered and
held, and it says which one a reply just named. `playback.py` captures what this PC is sending to
its own speakers, so a burst whose loudness follows it can be discounted — a streamer in Chrome is
otherwise indistinguishable from the user.
`brain_sdk.py` holds the persona and the session. `memory.py` is the profile, what Entity has learned, and the lexicon.
`chord.py` hears the modifier beside the spacebar + Enter, which no window on this machine can be
given — read its docstring before touching it; every claim in there was measured and several
obvious designs are wrong. The webview owns the main thread; the conversation, the dictation pump
and the keyboard hook run on workers, and the page's own poll is what drains the feed.

## Open work

Nothing is assigned. Outstanding in the profile's Enhancements: the rest of hearing only the user's
voice.

**Hearing only the user.** Nothing is built. Loopback gating WAS built and was taken back out the
same day, because it went deaf to him — the meter moved with his voice and not a word reached the
draft. That is the whole lesson, and it cost him an hour of a broken app: a false negative here is
far worse than a false positive, and the threshold that produced it had been fitted to a single
four-minute sample. Read `git log` for `playback.py` before rebuilding it. What was measured and
still holds:

- WASAPI loopback capture works, but not through `sounddevice` — its PortAudio build (19.7.0-devel)
  has no loopback flag and enumerates no loopback devices. `soundcard` does it.
- Speaker → air → mic on his machine is 90 ms, a clean correlation peak (r = .83 there, .47 either
  side). Comparing per-frame LOUDNESS survives the room; the waveform does not. Plain envelope
  correlation beat log and sqrt on labelled data.
- On one four-minute capture — his stream loud, him talking over it — the stream's bursts scored
  +0.38 to +0.96 against the delayed playback and his own −0.26 to +0.58. Replayed, a 0.6 bar took
  75 s of streamer-only from 7 draft lines to 0 and kept all twelve of his.
- And it still ate him live. So that sample did not generalise, the margin above his worst (0.583)
  was 0.017, and no bar fitted to one recording should be trusted. Whatever comes next needs paired
  captures across several sessions and volumes, and must fail toward hearing him.

Speaker enrollment is untouched. A voiceprint is personal: `runtime/`, never the source, and
bootstrapping is free — the chunks that became submitted turns in past sessions are labelled samples
of his voice. Same asymmetry, same decision point: `Burst`, beside `carries_speech`.

**Printing as it listens is done.** Parakeet has no streaming door — `recognize` takes a waveform
and reads all of it — so the burst so far is re-read as it grows, on a worker, because at 90 ms for
one second of speech and 640 ms for twenty it is thirty times faster than real time but nowhere near
cheap enough for the pump's thread. The readings are not fit to show raw: their tails are guesswork
the next reading rewrites, and four times in one three-second sentence the model answered a stretch
it could not place with nothing at all. Only what two readings running agree on goes up, and the line
never shrinks. Replayed at speaking speed through the real pump and the real Parakeet, his own
sentences reached the screen 2 to 5 seconds before the draft box used to fill.

**Printing as it listens is done.** Parakeet has no streaming door — `recognize` takes a waveform
and reads all of it — so the burst so far is re-read as it grows, on a worker, because at 90 ms for
one second of speech and 640 ms for twenty it is thirty times faster than real time but nowhere near
cheap enough for the pump's thread. The readings are not fit to show raw: their tails are guesswork
the next reading rewrites, and four times in one three-second sentence the model answered a stretch
it could not place with nothing at all. Only what two readings running agree on goes up, and the line
never shrinks. Replayed at speaking speed through the real pump and the real Parakeet, his own
sentences reached the screen 2 to 5 seconds before the draft box used to fill.

Driving the fleet is done. Which agent a piece of news is about now travels with it (`Outbox.News`)
rather than being read back out of the sentence, several ready at once are read out numbered, and
whichever is named is the one spoken. He was asked and chose numbering over a new brain directive,
because a marker is a thing that has reached him verbatim before.

## How the user works

They drive; you navigate. They are not technical outside code — any manual step needs literal,
click-by-click instructions, in Git Bash syntax, never PowerShell. They watch for the difference
between what you claim and what happened, and they are right about it more often than not.
