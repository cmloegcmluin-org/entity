/* The conversation page: draw the thread, follow the pointer with the copy button, and send what
   was said. The drawing itself is thread.js, which the agents' page uses too. */

const thread = document.getElementById("thread");
const contents = document.getElementById("contents");
const copier = document.getElementById("copy");
const draft = document.getElementById("draft");
const hearing = document.getElementById("hearing");
const mic = document.getElementById("mic");
const level = document.getElementById("level");

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
  offers = { node, text: whatToCopy(node, entries) };
  copier.hidden = false;
  copier.classList.remove("done");
  copier.classList.add("showing");
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
}

copier.addEventListener("click", async () => {
  if (!offers) return;
  await navigator.clipboard.writeText(offers.text);
  copier.classList.add("done");           // it says so, rather than leaving it a guess
  setTimeout(() => copier.classList.remove("done"), 1100);
});

thread.addEventListener("mouseover", (event) => {
  if (copier.contains(event.target)) return;  // reaching for it must not move it out from under
  const node = event.target.closest(".said, .day, .session");
  if (node && node.dataset.at !== undefined) offer(node, latest);
});
thread.addEventListener("mouseleave", () => copier.classList.remove("showing"));

/* ---- talking ------------------------------------------------------------------------------ */

async function post(where, body) {
  await fetch(where, { method: "POST", body: new URLSearchParams(body) });
}

const dictated = [];  // the chunks dictation put in the box, newest last, so "scratch that" can undo

function send() {
  const text = draft.value.trim();
  draft.value = "";
  dictated.length = 0;  // the box is empty; there is nothing left in it to take back
  if (text) post("/submit", { text });
}

/* Dictation types into the same box that is typed into by hand, joined readably. */
function dictate(chunks) {
  for (const chunk of chunks) {
    const so_far = draft.value;
    draft.value = so_far && !/[ \n]$/.test(so_far) ? `${so_far} ${chunk}` : so_far + chunk;
    dictated.push(chunk);
  }
  if (chunks.length) draft.scrollTop = draft.scrollHeight;
}

/* "Scratch that" - take back what he just said, out of the box it was typed into. Only while the
   chunk is still the end of what is in there: this is the box he also types in by hand, and
   guessing at what to cut from something he has edited would take away words he wrote himself. */
function retract(times) {
  for (let i = 0; i < times; i++) {
    const chunk = dictated[dictated.length - 1];
    const box = draft.value.replace(/[ \t]+$/, "");
    if (!chunk || !box.endsWith(chunk)) return;   // not taken off the stack unless it is used
    dictated.pop();
    draft.value = box.slice(0, box.length - chunk.length).replace(/[ \t]+$/, "");
  }
}

/* What he is being heard saying, while he is still saying it. Replaced whole rather than appended
   to, because the server sends the whole settled line each poll; it only ever grows, so the end is
   where the new words are and where it is scrolled to. */
function showHearing(text) {
  if (hearing.textContent === text) return;
  hearing.textContent = text;
  hearing.scrollTop = hearing.scrollHeight;
}

document.getElementById("send").onclick = send;
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

function showState(state, loudness) {
  mic.className = `btn ${state === "muted" ? "" : state}`;
  mic.textContent = { recording: "● listening", muted: "○ mic off", speaking: "◼ stop" }[state];
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
