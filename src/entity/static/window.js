/* The conversation page: draw the thread, follow the pointer with the copy button, and send what
   was said. The drawing itself is thread.js, which the agents' page uses too. */

const thread = document.getElementById("thread");
const contents = document.getElementById("contents");
const copier = document.getElementById("copy");
const draft = document.getElementById("draft");
const hearing = document.getElementById("hearing");
const mic = document.getElementById("mic");
const level = document.getElementById("level");

const linker = document.getElementById("copylink");

let drawn = 0;          // how many entries are already on the page
let offers = null;      // what the copy button would copy, and the element it belongs to

/* ---- the contents ------------------------------------------------------------------------- */

function listSessions(found) {
  if (contents.childElementCount === found.length) return;
  contents.replaceChildren();
  for (const session of found) {
    const row = document.createElement("button");
    row.type = "button";
    row.append(session.label);
    row.onclick = () => {
      const target = thread.querySelector(`[data-at="${session.at}"]`);
      if (!target) return;
      // Straight there, not smoothly: the archive is tens of thousands of pixels tall, and a
      // smooth scroll across that either takes seconds of blur or - as it did here - is dropped
      // on the floor, leaving the click looking like it did nothing at all.
      thread.scrollTop = target.offsetTop - 16;
      // Landing on a break at the top edge is indistinguishable from not having moved, so the
      // destination says it is the destination for a moment.
      for (const was of thread.querySelectorAll(".landed")) was.classList.remove("landed");
      void target.offsetWidth;  // restarts the animation when the same session is clicked twice
      target.classList.add("landed");
      for (const other of contents.children) other.removeAttribute("aria-current");
      row.setAttribute("aria-current", "true");
    };
    contents.append(row);
  }
}

/* ---- the copy button ---------------------------------------------------------------------- */

function offer(node, entries) {
  offers = { node, text: whatToCopy(node, entries), ref: referenceTo(node, entries) };
  copier.hidden = false;
  copier.classList.remove("done");
  copier.classList.add("showing");
  linker.hidden = !offers.ref;
  linker.classList.remove("done");
  linker.classList.add("showing");
  // Beside what is drawn: a message's box, or a break's mark - never the full-width row a
  // break sits in, which would put the button out at the far edge of an empty stretch.
  // Placed against the THREAD, since that is what it hangs inside: viewport coordinates would
  // leave it behind the moment the thread scrolled under it.
  const drawnPart = node.querySelector(".box, .mark") || node;
  const spot = drawnPart.getBoundingClientRect();
  const within = thread.getBoundingClientRect();
  const top = spot.top - within.top + thread.scrollTop;
  copier.style.top = `${top + spot.height / 2 - copier.offsetHeight / 2}px`;
  const left = spot.left - within.left + thread.scrollLeft;
  copier.style.left = node.classList.contains("right")
    ? `${left - copier.offsetWidth - 8}px`
    : `${left + spot.width + 8}px`;
  linker.style.top = `${parseFloat(copier.style.top) + copier.offsetHeight + 4}px`;
  linker.style.left = copier.style.left;
}

/* A LINK to a message is its header line - "Entity · 21:14:27" - the pointer he pastes back at
   Entity to name an exact moment of its own conversation. Only messages have one; a break is a
   place, not a moment. */
function referenceTo(node, entries) {
  const entry = entries[Number(node.dataset.at)];
  return entry && entry.bubble ? `${entry.name} · ${entry.stamp}` : "";
}

copier.addEventListener("click", async () => {
  if (!offers) return;
  await navigator.clipboard.writeText(offers.text);
  copier.classList.add("done");           // it says so, rather than leaving it a guess
  setTimeout(() => copier.classList.remove("done"), 1100);
});

linker.addEventListener("click", async () => {
  if (!offers || !offers.ref) return;
  await navigator.clipboard.writeText(offers.ref);
  linker.classList.add("done");
  setTimeout(() => linker.classList.remove("done"), 1100);
});

thread.addEventListener("mouseover", (event) => {
  if (copier.contains(event.target) || linker.contains(event.target)) return;
  const node = event.target.closest(".said, .day, .session");
  if (node && node.dataset.at !== undefined) offer(node, latest);
});
thread.addEventListener("mouseleave", () => {
  copier.classList.remove("showing");
  linker.classList.remove("showing");
});

/* ---- talking ------------------------------------------------------------------------------ */

async function post(where, body) {
  await fetch(where, { method: "POST", body: new URLSearchParams(body) });
}

const dictated = [];  // the chunks dictation put in the box, newest last, so "scratch that" can undo

/* The draft outlives the page. Every other tab is its own page load, so navigating away tore this
   one down and took the half-written turn with it - "the accumulated text has disappeared, but it
   should persist". The words are kept as they change and put back when the page returns; only
   sending or binning the draft lets go of them. (Binning fires an input event, so it is covered.) */
const KEPT_DRAFT = "entity-draft";
draft.value = sessionStorage.getItem(KEPT_DRAFT) || "";
const keepDraft = () => sessionStorage.setItem(KEPT_DRAFT, draft.value);
draft.addEventListener("input", keepDraft);

function send() {
  const text = draft.value.trim();
  draft.value = "";
  dictated.length = 0;  // the box is empty; there is nothing left in it to take back
  keepDraft();
  if (text) post("/submit", { text });
}

/* Dictation types into the same box that is typed into by hand, joined readably. */
function dictate(chunks) {
  for (const chunk of chunks) {
    const so_far = draft.value;
    draft.value = so_far && !/[ \n]$/.test(so_far) ? `${so_far} ${chunk}` : so_far + chunk;
    dictated.push(chunk);
  }
  if (chunks.length) {
    draft.scrollTop = draft.scrollHeight;
    keepDraft();
  }
}

/* "Scratch that" - take back what they just said, out of the box it was typed into. Only while the
   chunk is still the end of what is in there: this is the box they also types in by hand, and
   guessing at what to cut from something they have edited would take away words they wrote themselves. */
function retract(times) {
  for (let i = 0; i < times; i++) {
    const chunk = dictated[dictated.length - 1];
    const box = draft.value.replace(/[ \t]+$/, "");
    if (!chunk || !box.endsWith(chunk)) return;   // not taken off the stack unless it is used
    dictated.pop();
    draft.value = box.slice(0, box.length - chunk.length).replace(/[ \t]+$/, "");
  }
  keepDraft();
}

/* What they are being heard saying, while they are still saying it. Replaced whole rather than appended
   to, because the server sends the whole settled line each poll; it only ever grows, so the end is
   where the new words are and where it is scrolled to. */
function showHearing(text) {
  if (hearing.textContent === text) return;
  hearing.textContent = text;
  hearing.scrollTop = hearing.scrollHeight;
}

document.getElementById("send").onclick = send;

/* One click that says yes. About half their turns were the mic on, the word, the mic off and
   Submit - four gestures for one word. Whatever is half-written in the draft is left exactly
   where it is: losing typed words is the thing they filed a ticket about. */
document.getElementById("yes").onclick = () => post("/submit", { text: "Yes." });

/* And the bin beside it: the whole draft gone, undoably. Written through the document's own edit
   command rather than by assigning `.value`, because assigning wipes the undo stack with it and a
   button that destroys typed words for good is that same complaint again. */
document.getElementById("bin").onclick = () => {
  draft.focus();
  draft.select();
  if (!document.execCommand("delete")) draft.value = "";
  dictated.length = 0;
};
draft.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    send();
  }
});
mic.onclick = () => {
  if (mic.classList.contains("speaking")) return post("/stop", {});
  post("/mic", { recording: !mic.classList.contains("recording") });
};

const auto = document.getElementById("auto");
auto.onchange = () => post("/auto-listen", { on: auto.checked });

const LEVEL_FULL = 0.06;  // the meter tops out around loud speech, so ordinary talk moves it

let shownState = null;  // the poll repeats the state four times a second, and rewriting the
                        // button's class each time restarts its CSS animation at frame zero -
                        // which is why the recording pulse never visibly pulsed.
function showState(state, loudness) {
  if (state !== shownState) {
    shownState = state;
    mic.className = `btn ${state === "muted" ? "" : state}`;
    // Glyph plus the ACTION a click takes - "click the words 'mic off' to record" was backwards.
    mic.textContent = { recording: "■ stop", muted: "● record", speaking: "◼ stop" }[state];
    mic.title = { recording: "Stop recording", muted: "Start recording",
                  speaking: "Stop it talking" }[state];
  }
  level.style.width = state === "recording"
    ? `${Math.min(1, loudness / LEVEL_FULL) * 100}%`
    : "0";
}

/* ---- the poll ------------------------------------------------------------------------------ */

const latest = [];   // everything drawn, in order, so a copy can read a whole session back
let polling = false; // one poll at a time: two in flight both ask from the same place, and both
                     // draw what they are given - which is how one conversation became 52,000 rows

async function refresh() {
  if (polling) return;
  polling = true;
  try {
    const shown = await (await fetch(`/messages?since=${drawn}`)).json();
    latest.push(...shown.entries);
    drawInto(thread, shown.entries, shown.at);
    drawn = shown.total;
    listSessions(shown.sessions);
    showState(shown.state, shown.level);
    showHearing(shown.hearing || "");
    retract(shown.retract || 0);  // before the words beside it, which are always newer
    dictate(shown.dictated || []);
    if (shown.send) send();   // dictation said "over"
  } finally {
    polling = false;
  }
}

refresh();
setInterval(refresh, 400);


/* ---- the draft's right-click menu --------------------------------------------------------- */

/* The embedded browser offers no paste menu of its own in this box, and words don't only arrive
   by voice. The clipboard is read by the app itself (the server runs on this same machine), so
   no browser permission stands between the click and the paste. */
const pasteMenu = document.createElement("div");
pasteMenu.id = "paste-menu";
pasteMenu.hidden = true;
const pasteItem = document.createElement("button");
pasteItem.type = "button";
pasteItem.append("Paste");
pasteMenu.append(pasteItem);
document.body.append(pasteMenu);

draft.addEventListener("contextmenu", (event) => {
  event.preventDefault();
  pasteMenu.style.left = `${Math.min(event.clientX, innerWidth - 120)}px`;
  pasteMenu.style.top = `${Math.min(event.clientY, innerHeight - 48)}px`;
  pasteMenu.hidden = false;
});

pasteItem.addEventListener("click", async () => {
  pasteMenu.hidden = true;
  const response = await fetch("/clipboard");
  const { text } = await response.json();
  if (!text) return;
  draft.focus();
  draft.setRangeText(text, draft.selectionStart, draft.selectionEnd, "end");
  draft.dispatchEvent(new Event("input"));  // the kept-draft store must see the paste too
});

addEventListener("pointerdown", (event) => {
  if (!pasteMenu.contains(event.target)) pasteMenu.hidden = true;
});
addEventListener("keydown", (event) => {
  if (event.key === "Escape") pasteMenu.hidden = true;
});
