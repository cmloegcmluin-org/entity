# Working on Entity

Read this before touching anything here. It is what previous sessions learned the expensive way —
mostly by shipping something that looked right and having the user discover, again, that it wasn't.

Entity is a voice-in/voice-out companion that also supervises Claude coding agents on its user's
behalf. The point is not convenience; it is presence — someone to show up for — and a single voice
that shields the user from the machinery underneath. Almost every law below exists because that
shield tore somewhere.

## Land by opening a PR — the merge queue gates and merges it

**Never merge into the primary checkout, and never push to `main`.** You push your branch, open a
PR, and enqueue it. GitHub's merge queue builds the candidate merge of your PR onto the current
`main`, runs `.github/workflows/merge-gate.yml` on *that candidate*, and fast-forwards `main` only
if it is green — so what lands is exactly what was validated, even with several agents landing at
once, and there is no lock to hold.

**This replaces the global end-of-task sequence's merge step, and the `.git/agent-merge.lock` that
serializes it.** There is no local merge here, so no lock to take. Everything before it still
stands — commit, rebase, full green suite — then you push and open a PR instead of merging. The
gate runs the whole suite on `windows-latest`, the desk Entity runs on, so keep it green.

Work on a branch in a worktree (`git worktree add .claude/worktrees/<name> -b claude/<name>`),
never in the primary checkout. Sync by rebasing onto `origin/main` on a clean tree; never `reset`
to tidy or to sync.

```bash
# from your worktree, on your claude/<name> branch, with your work committed:
git fetch origin && git rebase origin/main    # rebase onto the LATEST main first
git push -u origin HEAD                        # --force-with-lease if the rebase rewrote pushed commits
gh pr create --fill --base main
gh pr merge --auto                             # enqueue; the queue lands it when the gate is green
                                               # --auto ALONE: --merge/--rebase/--squash trip "merge
                                               # strategy is set by the merge queue" and may not enqueue
```

**Enqueuing is not the finish line — landing is.** On a moving `main` a PR routinely goes `DIRTY`
or gets dropped from the queue on a red candidate, and then sits unmerged forever unless you act.
Watch both the candidate run *and* the PR's own checks: a `merge_group` failure never appears in
`gh pr checks`, and a failed `pull_request` check leaves auto-merge armed but never firing.

```bash
# Run in the background. Exits — and re-engages you — only when there is something to do:
#   0  merged           → report "PR #N merged" once, then stop
#   10 conflicts(DIRTY) → rebase onto origin/main, resolve inside the rebase, force-push, re-enqueue
#   11 candidate failed → read the merge_group run log, fix, push, re-enqueue
#   12 closed           → unexpected; surface to the user
#   13 PR check failed  → read the failing pull_request run log, fix, push (auto-merge stays armed)
pr=$(gh pr view --json number -q .number)
mg() { gh run list --event merge_group --limit 20 --json databaseId,status,conclusion,headBranch \
  -q "[.[]|select(.headBranch|contains(\"pr-$pr-\"))]|sort_by(.databaseId)|last|\"\(.databaseId) \(.conclusion//\"none\")\""; }
base=$(mg); base=${base%% *}; base=${base:-0}   # ignore candidate runs from superseded fixes
while :; do
  st=$(gh pr view "$pr" --json state -q .state)
  [ "$st" = MERGED ] && exit 0
  [ "$st" = CLOSED ] && exit 12
  [ "$(gh pr view "$pr" --json mergeStateStatus -q .mergeStateStatus)" = DIRTY ] && exit 10
  # A check that has actually concluded `fail` — not merely pending, which reads BLOCKED.
  if gh pr checks "$pr" 2>/dev/null | grep -qiw fail; then exit 13; fi
  latest=$(mg); rid=${latest%% *}
  if [ -n "$rid" ] && [ "${rid:-0}" -gt "$base" ] 2>/dev/null; then
    case "$latest" in *failure) exit 11;; esac
  fi
  sleep 45
done
```

**To update a branch that is still in the queue you must dequeue it first** — a
`push --force-with-lease` is rejected ("protected branch hook declined") while queued. Remove it,
then push and re-enqueue:

```bash
gh api graphql -f query='mutation($id:ID!){dequeuePullRequest(input:{id:$id}){mergeQueueEntry{position}}}' \
  -f id="$(gh pr view "$pr" --json id -q .id)"
```

**Delete your remote branch once the PR is terminal — but only on a positive merge check.**
Deleting the head branch of a still-open PR auto-closes it unmerged, and the work silently never
ships. Never key that on a watcher exit code:

```bash
gh pr view "$pr" --json state,mergedAt -q '.state + " " + (.mergedAt // "null")'
# delete ONLY when this prints "MERGED <timestamp>"
git push origin --delete "$br"
```

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
  test counts, not "I reran the suite myself". Every agent event (finished, died, wrote, quiet) goes
  through `narrator.py`: one trip through the brain, which composes the one or two sentences the
  user hears in its own voice. `relay.notice()` survives only as the fallback when the brain cannot
  answer — news must never die with a wedged session. Handed the raw stream, a person cannot tell
  whether they are talking to Entity or to the agent; the code, not the model, has to prevent it.
- **Brevity is the product.** The persona holds replies to a couple of short sentences, and the
  voice speaks them as they are written, so a barge-in is the user's own length limit. The old
  260-character cut and its told-you-it-was-cut system note are gone WITH their reason: they
  existed to manage a blocking brain and a robot voice that read whole replies at once. Do not
  reintroduce a cut without reintroducing that world.
- **Never speak while the user is mid-sentence.** Unprompted speech waits for the pause. It once
  broke in while someone was recording. (The mic being ARMED is not the test — the window's mic
  stays armed all conversation.)
- **No stock phrases.** "Got it.", "I'll get back to you on that.", "I've got a longer answer —
  ready for it?" and the still-processing check-ins are all deleted, by the user's request, after
  a year's worth of frustration in one week. Their reason to exist was a brain that blocked for
  30+ seconds; the streaming fast brain answers in the breath it was asked. Anything slower than
  a breath is an agent's job, dispatched and then narrated by the model in its own words.
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
delivery of agent news at a lull; it puts the desk's fleet briefing in front of the brain every
turn and streams the reply into the voice as it is written. `voice.py` is how a streamed reply
becomes audible — sentences cut the moment they end, synthesized and played while the next forms,
one stop draining everything — and `tts_neural.py` is the Kokoro engine behind it plus the
one-time model fetch into `runtime/tts/`, with the System.Speech robot voice serving until the
model is in. `actions.py` is everything the brain can DO: five typed in-process tools wired to
the desk — its speech carries no control phrases and its options carry no built-in tools.
`dictation.py` is the window's mic: a *state*, not a walkie-talkie — continuous
transcription into an editable draft, `hey entity` / `stop listening` to arm and disarm, `scratch
that` to take back what was just said, and it reports whether it is recording so nothing speaks
over the user. `hearing.py` is the live line: the burst so far, re-read on a worker of its own, with
a word shown only once two readings running have agreed on it — read its docstring before changing
any number in it, because every one was measured off real captured sessions. The window is a local
web app: `mirror.py` is the conversation as a window shows it — the message model,
the thread-safe feed everything crosses on, and where each session starts — with no window in it,
so all of it is tested without a display; `web.py` serves it, `templates/` and `static/` are the
pages, and `desktop.py` puts them in an OS window of their own (Flask on a loopback port, pywebview
holding the view) rather than a browser tab. `links.py` decides what a message names that can be
opened, and opens it. `agent_desk.py` holds each agent as a live session in-process (handles
used to be lost to context resets) and streams the whole exchange into its log; `steps.py` decides
what a streamed message becomes there — the agent's words as messages, and its commands, diffs and
output as the machinery under them, capped at both ends with what was dropped counted in place.
`waiting.py` is what happens when several agents finish at once: they are read out numbered and
held, and it says which one a reply just named. `narrator.py` is how any agent event becomes
speech: the desk, the inbox watcher and the quiet monitor emit typed events into it, the brain
words each one as its own sentence (composed news skips the unwritten-lines ledger - the brain
remembers what it wrote), and the plain capped notice is only the cannot-answer fallback.
`brain_sdk.py` holds the persona and the session: the FAST tier (Haiku), `tools=[]`, replies
streamed delta by delta — a talker that pulls levers, never an investigator; the agents it starts
are where Opus-tier work happens. `memory.py` is the profile, what Entity has learned, and the lexicon.
`chord.py` hears the modifier beside the spacebar + Enter, which no window on this machine can be
given — read its docstring before touching it; every claim in there was measured and several
obvious designs are wrong. The webview owns the main thread; the conversation, the dictation pump
and the keyboard hook run on workers, and the page's own poll is what drains the feed.

## Open work

Nothing is assigned. Outstanding in the profile's Enhancements: the rest of hearing only the user's
voice.

**Hearing only the user.** Nothing is built. Loopback gating WAS built and was taken back out the
same day, because it went deaf to the user — the meter moved with their voice and not a word reached
the draft. That is the whole lesson, and it cost an hour of a broken app: a false negative here is
far worse than a false positive, and the threshold that produced it had been fitted to a single
four-minute sample. Read `git log` for `playback.py` before rebuilding it. What was measured and
still holds:

- WASAPI loopback capture works, but not through `sounddevice` — its PortAudio build (19.7.0-devel)
  has no loopback flag and enumerates no loopback devices. `soundcard` does it.
- Speaker → air → mic on the test machine is 90 ms, a clean correlation peak (r = .83 there, .47
  either side). Comparing per-frame LOUDNESS survives the room; the waveform does not. Plain envelope
  correlation beat log and sqrt on labelled data.
- On one four-minute capture — a loud stream, the user talking over it — the stream's bursts scored
  +0.38 to +0.96 against the delayed playback and the user's own −0.26 to +0.58. Replayed, a 0.6 bar
  took 75 s of streamer-only from 7 draft lines to 0 and kept all twelve of the user's.
- And it still ate the user's speech live. So that sample did not generalise, the margin above its
  worst (0.583) was 0.017, and no bar fitted to one recording should be trusted. Whatever comes next
  needs paired captures across several sessions and volumes, and must fail toward hearing the user.

Speaker enrollment is untouched. A voiceprint is personal: `runtime/`, never the source, and
bootstrapping is free — the chunks that became submitted turns in past sessions are labelled samples
of the user's voice. Same asymmetry, same decision point: `Burst`, beside `carries_speech`.

**Printing as it listens is done.** Parakeet has no streaming door — `recognize` takes a waveform
and reads all of it — so the burst so far is re-read as it grows, on a worker, because at 90 ms for
one second of speech and 640 ms for twenty it is thirty times faster than real time but nowhere near
cheap enough for the pump's thread. The readings are not fit to show raw: their tails are guesswork
the next reading rewrites, and four times in one three-second sentence the model answered a stretch
it could not place with nothing at all. Only what two readings running agree on goes up, and the line
never shrinks. Replayed at speaking speed through the real pump and the real Parakeet, real
sentences reached the screen 2 to 5 seconds before the draft box used to fill.

Driving the fleet is done. Which agent a piece of news is about now travels with it (`Outbox.News`)
rather than being read back out of the sentence, several ready at once are read out numbered, and
whichever is named is the one spoken. Numbering was chosen over a new brain directive, because a
marker is a thing that has reached the user verbatim before.

## How the user works

They drive; you navigate. They are not technical outside code — any manual step needs literal,
click-by-click instructions, in Git Bash syntax, never PowerShell. They watch for the difference
between what you claim and what happened, and they are right about it more often than not.
