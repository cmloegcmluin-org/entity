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
| What an agent said, as it said it | `runtime/agent-logs/<name>.log` (retired ones move to `runtime/agent-logs-archive/`) | whether an agent is working or dead |
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

**Your repro is not his machine, and a fix he has not run is not fixed.** The close-dialog hang was
"fixed" twice on the strength of a repro that passed here, and it hung on his desk both times -
the repro lacked the live app's audio stack, worker threads and keyboard hook, and nothing said so.
Before asserting the cause of anything he experienced, align your story with HIS incident's
artifacts (timestamps, event log, transcripts) - not with a rebuilt approximation of it - and when
the evidence is a repro, SAY it was a repro. Report a landed change as "landed; unverified in your
hands" until he has exercised it. And when a failure can recur, make the app write its own evidence
at the moment of failure (the close stall dumps every thread to `runtime/close-stall.log`), so the
next diagnosis starts from fact instead of belief.

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
model is in. `actions.py` is everything the brain can DO: thirteen typed in-process tools wired to
the desk — among them update_persona and remember, its levers to edit its own persona overlay
(`runtime/persona.md`) and memory the way it files an enhancement — its speech carries no control
phrases and its options carry no built-in tools.
`polish.py` is the punctuation repairman: one Haiku session, every exchange on one asker thread
of its own, repairing the draft IN THE BACKGROUND as it grows (`precook`, re-handed the draft at
every pause) so the submit mostly stitches a finished repair plus a short bounded tail. Word-safe
by CODE via sequence alignment: respellings ("Maine"->"main") and joins ("Notes nook"->
"Notesnook") pass, anything eaten or invented refuses the repair whole - the old same-count rule
refused every join, which silently meant NO long dictation was ever repaired. Its prompt carries
his vocabulary (the model cannot fix "ideas" to Highdeas without knowing the name) and a worked
example, because the session learns from its own history: a warmup that needed no repair taught
it to echo, and it echoed his seventy-word request back untouched. Known residuals: the model
still sometimes declines name fixes, and a cold session's first repair can go through raw. `errands.py` is the quiet errand hand: small local chores - move a file, tidy a folder - run in one helper session with file tools, no agent tab, its outcome narrated like any other news. `foreman.py` is the senior layer between the talker and the workers: engaged only through the
brain's ask_foreman tool, one persistent Opus-high session that reads a stuck agent's task,
situation and log tail, settles technical snags itself through its one tell_agent tool (answering
"handled", which is swallowed), and escalates to the user only what is genuinely theirs — its
escalations go out app-authored so the unwritten-lines ledger keeps the fast brain aware of them.
`dictation.py` is the window's mic: a *state*, not a walkie-talkie — continuous
transcription into an editable draft, `hey entity` / `stop listening` to arm and disarm, `scratch
that` to take back what was just said, and it reports whether it is recording so nothing speaks
over the user. It is also the duplex ear: while the brain merely thinks the ear stays open and
words land in the draft; while the voice is actually sounding, chunks are judged against the
script being spoken (`covered_by`) — its own leak dropped, other words kept, and only a stop bark
cuts the audio, so the TV can never kill a reply. `hearing.py` is the live line: the burst so far, re-read on a worker of its own, with
a word shown only once two readings running have agreed on it — read its docstring before changing
any number in it, because every one was measured off real captured sessions. The window is a local
web app: `mirror.py` is the conversation as a window shows it — the message model,
the thread-safe feed everything crosses on, and where each session starts — with no window in it,
so all of it is tested without a display; `web.py` serves it, `templates/` and `static/` are the
pages (three: the conversation, Config — one page holding what were the Profile, Memory, Persona
and Translations tabs, with a contents rail, the old tab paths redirecting into it; Life context
and Memory are bullet lists, not checklists, and his translation and instruction edits are in
force immediately — translations swap into the running ear on save, instructions ride the
per-turn notes — and Agents),
and `desktop.py` puts them in an OS window of their own (Flask on a loopback port, pywebview
holding the view) rather than a browser tab — reopening where it was last closed unless that
monitor is gone, its X answered by the page's own styled dialog (asked OFF the GUI thread: evaluate_js inside the
closing event waits on plumbing that needs that same thread, and the inline ask froze the X press
itself) (the native confirm was a
light-mode box in a dark app; only the dialog's Close, through `Controls.quit`, actually closes),
and its bar carrying a Restart-to-upgrade button that appears only when the checkout on disk has
moved past the booted commit (worktrees.head_commit, polled by the page) - the relaunch is a
DETACHED helper (`relauncher.py`) spawned at the moment of the request, which waits for the old
pid to die however it dies and then starts the new app, because relaunching as the old process's
last act meant no relaunch at all when teardown misbehaved; window teardown answers the /quit request before
destroying, and waves its OWN destroy through the closing event (destroy fires that same event,
and answering it with the dialog question against a dying page hung the GUI thread — twice), and
the main thread waits the session worker out so native audio is never torn down under a live
thread. The memory store is an INBOX he works to zero:
`review.py` raises one remembered fact for his verdict in genuine downtime (fleet idle, the
transcript quiet a few minutes - its mtime is the clock), each fact once per session, never two
nudges close together; the brain words it (narrator "memory") and settles the verdict with
forget_memory / update_persona. The old Vocabulary card is gone: vocabulary IS translation, shown
as rows whose left side is "(paraphone)" (para + phone: anything sounding close enough), reading
and writing the lexicon (`reconcile_lexicon` - folder-scanned terms pass through untouched) and
retuning the running ear on save. The credit warning was tried and DROPPED
by his call: the local records count tokens, Anthropic's real weekly meter is percentages it does
not expose locally, and a warning measured against a guessed denominator fires wrong in both
directions — do not rebuild it without a sanctioned usage source. The app presents as
"Excephalon" everywhere he sees or hears it — title, icon (the Chaosphere: a brain in a spiked
wire cage, drawn transparent in the two-app family palette — gray-green metal, light-pink brain —
shared with Highdeas's leaf-and-mic), launcher `Excephalon.bat`, the persona's own name, and the wake phrase ("hey
excephalon", with "hey entity" kept working because the transcriber only sometimes lands the
coined word — it is in the vocabulary to help) — while the repo, the module, the transcript
line format and every internal role key stay `entity`: renaming those breaks parsers of past
transcripts for a word nobody hears. `links.py` decides what a message names that can be
opened, and opens it. `agent_desk.py` holds each agent as a live session in-process (handles
used to be lost to context resets), streams the whole exchange into its log, records the fleet in
`runtime/agents.json` and revives it on startup — each agent resumed by CLI session id, one caught
mid-task told to pick back up, one recorded mid-landing told to settle the merge NOW and watch it
in the foreground (a backgrounded watch once ended the turn, nothing re-engages an idle agent, and
the merged report never existed) — its digest also names tabs whose log files linger with no agent
behind them, because the window draws a tab per log file and a brain briefed from the desk alone
once could not see the tab the user was pointing at; and `retire()` wraps a finished agent up whole: its log moved to
the fleet's one archive (`runtime/agent-logs-archive/`, a SIBLING of the live folder so an archived
log is outside what the roster globs and can never come back as a tab — `tailing.archive_dir` names
it in one place, shared with the window's own close button), the Enhancements item it was completing
ticked off the user's list, its session closed, its worktree removed. That item rides with the
agent from `start` (the brain passes it to `start_agent` when the work is one off the list) and is
ticked only for a cleanly finished agent, never a died one, because a wrong tick would corrupt the
list's record of ask and answer. Every task the desk hands out carries the standing
rules (rebase before presenting, present for the user's EYES, the engineering law in brief) and a
pointer to the machine-wide engineering law file when one exists (`law_path`, home-relative in
`__main__` so nothing personal enters the source); agents load their repo's checked-in CLAUDE.md
(`setting_sources=["project"]`) and never the user's personal config, whose conversation rules
and reply-format hook break a coding agent. `delivery.py` is the review loop as code — building →
presented-with-steps → landing, a verdict impossible on work never presented, approval dispatching
the landing and rejection the feedback mechanically, so the loop's order is a rule rather than a
persona habit; `steps.py` decides
what a streamed message becomes there — the agent's words as messages, and its commands, diffs and
output as the machinery under them, capped at both ends with what was dropped counted in place.
`waiting.py` is what happens when several agents finish at once: they are read out numbered and
held, and it says which one a reply just named. `narrator.py` is how any agent event becomes
speech: the desk, the inbox watcher and the quiet monitor emit typed events into it, the brain
words each one as its own sentence (composed news skips the unwritten-lines ledger - the brain
remembers what it wrote), and the plain capped notice is the fallback when the brain cannot answer
- or answers too late: each narration's wait is bounded, because one hung narration once held the
brain's lock with the merge report and the quiet warning queued behind it until the app closed and
all of it died unspoken.
`brain_sdk.py` holds the persona and the session: the FAST tier (Haiku), `tools=[]`, replies
streamed delta by delta — a talker that pulls levers, never an investigator; the agents it starts
are where Opus-tier work happens. Its every ask is bounded, and so is waiting for its one-at-a-time
lock: a stream once died without raising, held the lock from inside a narration, and everything
after — the merged report, a direct question, every later submission — sat at "(thinking…)"
forever; now the deadline sheds the dead session (closing it makes the stranded ask raise, which
frees the lock) and the turn retries once on a fresh seeded session before it ever gives up. `memory.py` is the profile, what Entity has learned, and the lexicon.
`chord.py` hears the modifier beside the spacebar + Enter, which no window on this machine can be
given — read its docstring before touching it; every claim in there was measured and several
obvious designs are wrong. The webview owns the main thread; the conversation, the dictation pump
and the keyboard hook run on workers, and the page's own poll is what drains the feed.

## Open work

Nothing is assigned. Outstanding in the profile's Enhancements: the rest of hearing only the user's
voice.

**Hearing only the user.** The measuring half now exists: `voiceprint.py` learns the user's voice
from one minute of them reading (`Learn my voice.bat` at the repo root records it, keeps the raw
wav in `runtime/voice/` for future re-learning, saves the averaged speaker embedding) and scores
any audio against it — sherpa-onnx CAM++ (`runtime/voice/wespeaker_en_voxceleb_CAM++.onnx`, 28 MB;
torch stacks don't install on this Python). Measured on real session audio: the model separates
voices (Entity against its own print ~0.55–0.95; a mostly-him session against Entity's print
median 0.18, and the high outliers in that set were literally Entity's replies leaking through the
speakers into the armed mic) — but a print scraped from UNLABELED session audio matches everything
a little, so enrollment is the clean recording, never scraped bootstrapping. No score DECIDES
anything yet: `score()` yields None without a print, callers keep the words, and the dropping
threshold gets chosen only from scores logged across real sessions - which the window's pump now
collects: `Scorekeeper` (wired into `Dictation`) scores every worded chunk on a worker of its own
into `runtime/voice/scores-*.log`, score beside words, a no-op until the minute is recorded. Loopback gating WAS built once
and was taken back out the same day, because it went deaf to the user — the meter moved with their
voice and not a word reached the draft. That is the whole lesson, and it cost an hour of a broken
app: a false negative here is far worse than a false positive, and the threshold that produced it
had been fitted to a single four-minute sample. Read `git log` for `playback.py` before rebuilding
it. What was measured and still holds:

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
