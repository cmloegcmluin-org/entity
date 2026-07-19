# Working on Entity

Read this before touching anything here. It is what previous sessions learned the expensive way —
mostly by shipping something that looked right and having the user discover, again, that it wasn't.

Entity is a voice-in/voice-out companion that also supervises Claude coding agents on the user's
behalf. The point is not convenience; it is presence — someone to show up for — and a single voice
that shields him from the machinery underneath. Almost every law below exists because that shield
tore somewhere.

## Read the evidence. Never ask him to paste it.

Every session leaves artifacts. Use them before forming any theory:

| What | Where | Answers |
|---|---|---|
| What he saw on screen | `runtime/transcripts/session-*.log` | every printed and spoken line, timestamped |
| What the mic actually heard | `runtime/audio/session-*.wav` | whether a word reached the machine at all |
| What an agent said, as it said it | `runtime/agent-logs/<name>.log` | whether an agent is working or dead |
| Who is running right now | `runtime/active-agents.txt` | the roster, with last-heard times |
| What Entity knows about him | `runtime/profile.md`, `runtime/learned.md` | his standing context; both gitignored |

Asking him to copy his scrollback is a defect in this project — the transcript exists precisely so
nobody ever has to. Reading the transcript is also how you check your own work: several "fixed"
claims were disproved by the transcript in the next message.

**Diagnose from the artifact, never from the code's intent.** The two most expensive wrong answers
in this project's history were both confident stories told without looking: an agent declared "dead"
that answered 43 seconds later, and a freeze blamed on a phrase rather than the latched flag that
caused it. If you cannot observe something, say so and ask for the one observation that would settle
it. A plausible reconciliation is worse than an admitted gap, because he acts on it.

## The rules Entity lives by

These are user requirements, learned through failures he had to sit through. Persona text enforces
some; code enforces the rest, and where only the persona enforces something, treat that as a known
weakness rather than a solution.

- **Insulate him from agents.** An agent's own words never reach him — not commit hashes, not test
  counts, not "I reran the suite myself". `relay.notice()` is the only door: agent name, first
  sentence, capped, and a pointer to its tab. He could not tell whether he was talking to Entity or
  to the agent, and said so; the code, not the model, has to prevent it.
- **Brevity is the product.** A reply is cut at a sentence past ~260 characters, and the next turn
  tells the model it was cut. Long answers are not "delivered differently" — they are lost.
- **Never speak while his mic is on.** Unprompted speech and finished background answers both wait.
  It once broke in mid-sentence while he was recording.
- **One handoff line, verbatim:** "I'll get back to you on that." after 5 seconds. No variations, no
  periodic progress updates — he found both worse than silence.
- **Never self-certify.** Green tests are not verification; his eyes are. Put the real thing in front
  of him, or give him the exact steps, and let him judge. And never present work for verification
  while a setup step of his is still outstanding.
- **When he says something isn't there, it isn't.** He is looking at the screen and you are not.

## Failure patterns that have recurred here

- **A mechanism nobody perceives.** Truncation that the model never sees teaches it nothing; a layout
  "capped at half width" is worthless if the framework ignores the property. Before calling such a
  change done, name the recipient and state how the signal reaches them. Tests that assert the
  mechanism fired are not evidence anything received it. The chat bubbles took four attempts for
  exactly this reason — the wrap was measured and correct while the tint still painted edge to edge,
  and only screenshotting the pane and reading the pixels back showed it.
- **Latched flags.** `Outbox.arrived` is cleared only by draining. Any path that decides not to
  deliver must still drain, or the window's mic yields empty turns forever and his submissions are
  never read. That froze a whole session.
- **Fan-out where he named one thing.** A worktree is recognized by its `.git`; globbing a directory
  once started an agent in `.venv`, `docs` and `src` of a single worktree.
- **Believing the model over the file.** Entity has claimed to have filed something, opened
  something, or verified something that had not happened. Check the artifact.

## Shape of the code

`conversation.py` is the loop (listen → think → speak) and owns turn-taking, barge-in, and the
5-second handoff. `dictation.py` is the window's mic: a *state*, not a walkie-talkie — continuous
transcription into an editable draft, `hey entity` / `stop listening` to arm and disarm, and it
reports whether it is recording so nothing speaks over him. `gui.py` mirrors everything through one
thread-safe feed into Tk; anything that can be wrong lives outside Tk and is tested without a
display. `bubbles.py` is one tinted box per message — a real widget, because a Tk tag's background
paints the whole line box. `agent_desk.py` holds each agent as a live session in-process (handles
used to be lost to context resets) and streams its steps into its log. `brain_sdk.py` holds the
persona and the session. `memory.py` is his profile and what Entity has learned. `chord.py` hears
the key beside his spacebar + Enter, which no window on this machine can be given — read its
docstring before touching it; every claim in there was measured and several obvious designs are
wrong. Tk touches only the main thread; the conversation, the dictation pump and the keyboard hook
run on workers.

## Open work

One task is running or queued as its own session — check with him before starting it:

1. **Sanitization for a public repo.** Nothing under `runtime/` is tracked or has ever been
   committed, but the source is written about him by name, with his projects, hardware, and profile
   fragments in test fixtures. Blocks moving this to the `example-org` GitHub organization and
   adopting the PR/merge-queue process used by `sample-lib`, `bayesian-main` and `notecraft`.

Also outstanding, not yet assigned: hearing only his voice (speaker enrollment, and loopback gating
so audio this PC is playing is discounted), and a live word-by-word transcription display with a
spoken codeword to rewind and re-say. Both are in his Enhancements tab.

## How he works

He drives; you navigate. He is not technical outside code — any manual step needs literal,
click-by-click instructions, in Git Bash syntax, never PowerShell. He watches for the difference
between what you claim and what happened, and he is right about it more often than not.
