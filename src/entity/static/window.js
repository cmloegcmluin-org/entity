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
  if (entry.role === "day") {
    const day = document.createElement("div");
    day.className = "day";
    day.append(entry.stamp);
    return day;
  }
  if (entry.role === "session") {
    const session = document.createElement("div");
    session.className = "session";
    session.append("• • •");
    return session;
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

function draw(entries) {
  const atEnd = thread.scrollTop + thread.clientHeight >= thread.scrollHeight - 40;
  for (const entry of entries.slice(drawn)) {
    const node = element(entry);
    node.dataset.at = entries.indexOf(entry);
    thread.append(node);
  }
  drawn = entries.length;
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
      if (target) target.scrollIntoView({ block: "start", behavior: "smooth" });
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
  const box = node.classList.contains("said") ? node.querySelector(".box") : node;
  const spot = box.getBoundingClientRect();
  const beside = node.classList.contains("right");
  copier.style.top = `${spot.top + spot.height / 2 - copier.offsetHeight / 2}px`;
  copier.style.left = beside
    ? `${spot.left - copier.offsetWidth - 8}px`
    : `${(node.classList.contains("said") ? spot.right : spot.right - 40) + 8}px`;
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

let latest = [];

async function refresh() {
  const shown = await (await fetch("/messages")).json();
  latest = shown.entries;
  draw(shown.entries);
  listSessions(shown.sessions);
  showState(shown.state);
}

refresh();
setInterval(refresh, 400);
