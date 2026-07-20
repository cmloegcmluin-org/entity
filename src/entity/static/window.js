/* The window's behaviour: draw the conversation, follow the pointer with the copy button, and
   send what was typed.

   The server hands over entries that already know who said them and which side they belong on,
   so nothing here parses a transcript line. */

const thread = document.getElementById("thread");
const contents = document.getElementById("contents");
const copier = document.getElementById("copy");
const draft = document.getElementById("draft");
const mic = document.getElementById("mic");
const level = document.getElementById("level");

let drawn = 0;          // how many entries are already on the page
let offers = null;      // what the copy button would copy, and the element it belongs to

/* ---- drawing ------------------------------------------------------------------------------ */

function element(entry) {
  // A break is a full-width row so it can be hovered anywhere along it, holding an inner mark
  // that is only as wide as what is drawn - which is what the copy button has to sit beside.
  if (entry.role === "day" || entry.role === "session") {
    const row = document.createElement("div");
    row.className = entry.role;
    const mark = document.createElement("span");
    mark.className = "mark";
    mark.append(entry.role === "day" ? entry.stamp : entry.label);
    row.append(mark);
    return row;
  }
  if (!entry.bubble) {
    const aside = document.createElement("div");
    aside.className = "aside";
    aside.append(entry.text);
    return aside;
  }
  const said = document.createElement("div");
  said.className = `said ${entry.side}${entry.historical ? " historical" : ""}`;
  const who = document.createElement("div");
  who.className = "who";
  who.append(`${entry.name} · ${entry.stamp}`);
  const box = document.createElement("div");
  box.className = "box";
  box.append(entry.text);
  said.append(who, box);
  return said;
}

function draw(fresh, at) {
  // Nothing new means nothing to do. Following the live end on an EMPTY poll re-pinned the
  // thread to the bottom four times a second, which quietly cancelled every scroll the contents
  // started - the jump happened and was undone before it could be seen.
  if (!fresh.length) return;
  const atEnd = thread.scrollTop + thread.clientHeight >= thread.scrollHeight - 40;
  fresh.forEach((entry, index) => {
    const node = element(entry);
    node.dataset.at = at + index;
    thread.append(node);
  });
  if (atEnd) thread.scrollTop = thread.scrollHeight;
}

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
      // Landing on a dim rule at the top edge is indistinguishable from not having moved, so the
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

/* What copying this element means: a message is its own words; a break is the whole session it
   heads, up to the next break. */
function whatToCopy(node, entries) {
  const at = Number(node.dataset.at);
  if (entries[at].bubble) return entries[at].text;
  const said = [];
  for (const entry of entries.slice(at + 1)) {
    if (entry.role === "session" || entry.role === "day") break;
    said.push(entry.bubble ? `${entry.name} · ${entry.stamp}\n${entry.text}` : entry.text);
  }
  return said.join("\n");
}

function offer(node, entries) {
  offers = { node, text: whatToCopy(node, entries) };
  copier.hidden = false;
  copier.classList.remove("done");
  copier.classList.add("showing");
  // Beside what is drawn: a message's box, or a break's mark - never the full-width row a
  // break sits in, which would put the button out at the far edge of an empty stretch.
  // Placed against the THREAD, since that is what it now hangs inside: viewport coordinates
  // would leave it behind the moment the thread scrolled under it.
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

function withdraw() {
  copier.classList.remove("showing");
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
thread.addEventListener("mouseleave", withdraw);

/* ---- talking ------------------------------------------------------------------------------ */

async function post(where, body) {
  await fetch(where, { method: "POST", body: new URLSearchParams(body) });
}

function send() {
  const text = draft.value.trim();
  draft.value = "";
  if (text) post("/submit", { text });
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

function showState(state) {
  mic.className = `btn ${state === "muted" ? "" : state}`;
  mic.textContent = { recording: "● listening", muted: "○ mic off", speaking: "◼ stop" }[state];
  if (state !== "recording") level.style.width = "0";
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
    draw(shown.entries, shown.at);
    drawn = shown.total;
    listSessions(shown.sessions);
    showState(shown.state);
  } finally {
    polling = false;
  }
}

refresh();
setInterval(refresh, 400);
