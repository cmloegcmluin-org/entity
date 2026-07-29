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
  offers = { node, text: whatToCopy(node, entries), url: linkTo(node, entries) };
  copier.hidden = false;
  copier.classList.remove("done");
  copier.classList.add("showing");
  linker.hidden = !offers.url;
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

/* The link button copies an actual URL with an anchor hash - http://127.0.0.1:5199/#at=<moment> -
   that reopens the conversation at this exact turn, not just the readable text. `moment` (the
   date and time, no name) comes from the server; the page encodes it into the hash of THIS
   instance's own origin, so the URL points at whatever port the window is on. Only messages have
   one; a break is a place, not a moment. */
function linkTo(node, entries) {
  const entry = entries[Number(node.dataset.at)];
  return entry && entry.moment ? `${location.origin}/#at=${encodeURIComponent(entry.moment)}` : "";
}

copier.addEventListener("click", async () => {
  if (!offers) return;
  await navigator.clipboard.writeText(offers.text);
  copier.classList.add("done");           // it says so, rather than leaving it a guess
  setTimeout(() => copier.classList.remove("done"), 1100);
});

linker.addEventListener("click", async () => {
  if (!offers || !offers.url) return;
  await navigator.clipboard.writeText(offers.url);
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
    if (state === "waking") {
      // The server is up but the mic isn't: its models are still loading. Stay dimmed and
      // unclickable - enabling here is what let clicks die silently for the first seconds.
      mic.disabled = true;
      mic.title = "Excephalon is still waking up";
    } else {
      mic.disabled = false;  // the pump's first report proves the mic exists
      mic.className = `btn ${state === "muted" ? "" : state}`;
      // Glyph plus the ACTION a click takes - "click the words 'mic off' to record" was backwards.
      mic.textContent = { recording: "■ stop", muted: "● record", speaking: "◼ stop" }[state];
      mic.title = { recording: "Stop recording", muted: "Start recording",
                    speaking: "Stop it talking" }[state];
    }
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

refresh().then(jumpToHash);   // once the thread is drawn, honour a #at= we were opened with
setInterval(refresh, 400);
addEventListener("hashchange", jumpToHash);


/* ---- a link to a moment: #at=<date time> --------------------------------------------------- */

/* A "(filed …)" link from the Config page lands here as #at=2026-07-28 02:23, naming the moment an
   item was filed. Once the thread is drawn, go to that turn and mark it the way the contents list
   marks a session it sends us to - a break at the top edge is otherwise indistinguishable from not
   having moved. */
function jumpToHash() {
  const at = new URLSearchParams(location.hash.slice(1)).get("at");
  const target = at ? turnAt(at) : null;
  const node = target === null ? null : thread.querySelector(`[data-at="${target}"]`);
  if (!node) return;
  thread.scrollTop = node.offsetTop - 16;
  for (const was of thread.querySelectorAll(".landed")) was.classList.remove("landed");
  void node.offsetWidth;          // restart the highlight if the same link is followed twice
  node.classList.add("landed");
}

/* Which drawn entry a "date HH:MM(:SS)" pointer names. A link button's URL carries the full second,
   so it lands on the exact turn even when two share a minute; a filing stamp carries only the
   minute, so it lands on the first turn of that minute. Either way the stamp is matched by prefix,
   and failing that it falls back to the day's own break so he lands on the right day at least. */
function turnAt(pointer) {
  const [date, time = ""] = pointer.trim().split(" ");
  let day = "";
  for (let at = 0; at < latest.length; at++) {
    if (latest[at].role === "day") day = latest[at].stamp;
    if (latest[at].bubble && day === date && latest[at].stamp.startsWith(time)) return at;
  }
  for (let at = 0; at < latest.length; at++) {
    if (latest[at].role === "day" && latest[at].stamp === date) return at;
  }
  return null;
}


/* ---- the right-click menus ---------------------------------------------------------------- */

/* One little menu, shaped once and used twice: the draft box's (Paste and Copy) and a message
   header's (Copy). Items are [label, action]; it opens at the cursor and is torn down on any click
   away or Escape. The embedded browser offers no menu of its own here, and writing the clipboard
   is allowed even where reading it needs a permission nobody is there to grant - so Copy runs in
   the page while Paste asks the app (which runs on this same machine) to read it back. */
function popupMenu(items) {
  const menu = document.createElement("div");
  menu.className = "popmenu";
  menu.hidden = true;
  for (const [label, action] of items) {
    const button = document.createElement("button");
    button.type = "button";
    button.append(label);
    button.addEventListener("click", () => { menu.hidden = true; action(); });
    menu.append(button);
  }
  document.body.append(menu);
  return menu;
}

function openMenu(menu, event) {
  event.preventDefault();
  menu.hidden = false;  // shown first, so its measured height keeps it clear of the bottom edge
  menu.style.left = `${Math.min(event.clientX, innerWidth - menu.offsetWidth - 8)}px`;
  menu.style.top = `${Math.min(event.clientY, innerHeight - menu.offsetHeight - 8)}px`;
}

/* The draft box: paste what the app reads off the clipboard, or copy what is picked out of the box
   (or the whole box, if nothing is selected). */
async function pasteIntoDraft() {
  const { text } = await (await fetch("/clipboard")).json();
  if (!text) return;
  draft.focus();
  draft.setRangeText(text, draft.selectionStart, draft.selectionEnd, "end");
  draft.dispatchEvent(new Event("input"));  // the kept-draft store must see the paste too
}
function copyFromDraft() {
  const picked = draft.value.slice(draft.selectionStart, draft.selectionEnd) || draft.value;
  if (picked) navigator.clipboard.writeText(picked);
}
const draftMenu = popupMenu([["Paste", pasteIntoDraft], ["Copy", copyFromDraft]]);
draft.addEventListener("contextmenu", (event) => openMenu(draftMenu, event));

/* A message header ("You · 05:01:59"): right-click to copy the same dated pointer the link button
   copies - the useful thing to paste back at Entity, since the header on screen shows only the
   time. The header is selectable too (see .who in app.css), so a plain drag-select still works. */
let headerReference = "";
const headerMenu = popupMenu([["Copy", () => headerReference
  && navigator.clipboard.writeText(headerReference)]]);
thread.addEventListener("contextmenu", (event) => {
  const said = event.target.closest(".who")?.closest(".said");
  const entry = said && said.dataset.at !== undefined ? latest[Number(said.dataset.at)] : null;
  if (!entry || !entry.reference) return;   // not a datable message header - leave the default menu
  headerReference = entry.reference;
  openMenu(headerMenu, event);
});

/* Any click away, or Escape, puts every open menu away. */
addEventListener("pointerdown", (event) => {
  for (const menu of document.querySelectorAll(".popmenu")) {
    if (!menu.contains(event.target)) menu.hidden = true;
  }
});
addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    for (const menu of document.querySelectorAll(".popmenu")) menu.hidden = true;
  }
});
