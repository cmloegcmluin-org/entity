/* The pages that are edited in place: the profile's lists, what Entity has learned, and the words
   it swaps.

   There is no Save button, because a document you have to remember to save is one you lose. It
   writes back when typing stops, and says so - a save nobody can see is one nobody trusts.

   And it writes back BEFORE the page goes. A save half a second after the last keystroke never
   happens at all if the next thing you do is click another page in the bar, which is exactly how
   the profile lost everything they added to it: "I add new items, tab away, tab back, and they're
   just gone." So whatever is still waiting goes out the moment what they are editing loses focus,
   and again on the way off the page - by beacon, the only send that outlives the page that
   started it. */

const saved = document.getElementById("saved");
const AFTER = 500;   // milliseconds of not typing before what is written goes back

function announce(what) {
  saved.textContent = what;
  saved.classList.add("showing");
  setTimeout(() => saved.classList.remove("showing"), 1600);
}

/* Two shapes of thing to send, and one way to send either: a box of text is the field it holds,
   a list is its items. `fetch` and `sendBeacon` both read the encoding off the body itself. */
const asForm = (fields) => new URLSearchParams(fields);
const asJson = (payload) => new Blob([JSON.stringify(payload)], { type: "application/json" });

async function post(where, body, leaving) {
  if (leaving) {
    navigator.sendBeacon(where, body);   // the page is going; nothing that waits for a reply lands
    return;
  }
  await fetch(where, { method: "POST", body });
  announce("Saved");
}

/* ---- what is still waiting to be written back ----------------------------------------------- */

const waiting = new Map();   // the box or list being edited -> what will save it, and when

function soon(what, save) {
  clearTimeout(waiting.get(what)?.timer);
  waiting.set(what, { save, timer: setTimeout(() => flush(what), AFTER) });
}

function flush(what, leaving) {
  const held = waiting.get(what);
  if (!held) return;
  clearTimeout(held.timer);
  waiting.delete(what);
  held.save(leaving);
}

/* Ticking a box is not typing: there is no pause to wait out, so it goes now - and drops whatever
   was still on the timer, which is the same save reading the same list. */
function atOnce(what, save) {
  clearTimeout(waiting.get(what)?.timer);
  waiting.delete(what);
  save(false);
}

/* A click on another page in the bar takes this one away with the edit still on its timer. Both of
   these fire while the page can still speak: `pagehide` is its last word, and a window merely
   hidden - they switched to something else - may never come back to fire anything. */
const flushAll = () => { for (const what of [...waiting.keys()]) flush(what, true); };
addEventListener("pagehide", flushAll);
document.addEventListener("visibilitychange", () => { if (document.hidden) flushAll(); });

/* ---- the boxes of text: what Entity has learned, and the words it swaps ---------------------- */

for (const box of document.querySelectorAll(".writing")) {
  // Which page this box belongs to, and so where it writes itself back to.
  const where = box.dataset.persona ? "/persona" : box.dataset.learned ? "/memory" : "/translations";
  const save = (leaving) => post(where, asForm({ body: box.value }), leaving);
  box.addEventListener("input", () => soon(box, save));
  box.addEventListener("blur", () => flush(box));
}

/* ---- the profile's lists: a box to tick, and the words beside it ----------------------------- */

const rowsOf = (list) => [...list.querySelectorAll("li")];
const wordsOf = (row) => row.querySelector(".item");
/* The row carries its stable id as `data-id`, so a save sends it back and the same item keeps the
   same number. A row he has just made has none yet; the server hands it the next one. */
const itemsOf = (list) => rowsOf(list).map((row) => ({
  id: row.dataset.id ? Number(row.dataset.id) : null,
  done: row.querySelector("input").checked,
  text: wordsOf(row).textContent,
}));

/* A fresh, empty row shaped like an existing one. A new item carries none of the id the row it was
   cloned from has, so the server numbers it anew rather than two rows claiming one number. The page
   is the one place a row's shape is written, so a new row is made by cloning, never by building. */
function freshRow(like) {
  const row = like.cloneNode(true);
  row.className = "";
  row.querySelector("input").checked = false;
  row.removeAttribute("data-id");
  // The number's spot is reserved from the first keystroke - typing used to start in the space
  // where the id belongs - and the placeholder becomes the real number when the save assigns it.
  let tag = row.querySelector(".tag");
  if (!tag) {
    tag = document.createElement("span");
    tag.className = "tag";
    row.querySelector("input").after(tag);
  }
  tag.classList.add("pending");
  tag.textContent = "#·";
  wordsOf(row).textContent = "";
  return row;
}

/* Pasting a block is how a list arrives from somewhere else - a page, a doc, another list. Each
   line becomes its own item ("assume any newline is a checklist item when pasting"), and a line
   that comes punctuated as a bullet is stripped back to its words, so a pasted bullet list becomes
   the items it reads as instead of smooshing into one note. */
const LIST_MARKER = /^\s*(?:[-*•]|\d+[.)])\s+/;
const pastedLines = (text) =>
  text.split(/\r?\n/).map((line) => line.replace(LIST_MARKER, "").trim()).filter(Boolean);

/* Put the caret in a row, at one end or the other of what it says. */
function caretTo(row, atStart) {
  const words = wordsOf(row);
  words.focus();
  const spot = document.createRange();
  spot.selectNodeContents(words);
  spot.collapse(atStart);
  const chosen = getSelection();
  chosen.removeAllRanges();
  chosen.addRange(spot);
}

const lengthBefore = (words, node, offset) => {
  const spot = document.createRange();
  spot.selectNodeContents(words);
  spot.setEnd(node, offset);
  return spot.toString().length;
};

/* Where the caret is in a row's words, as the start and end of what is selected - the same number
   twice when nothing is. Measured as the length of the text before each point rather than as an
   offset into a node, because the words are a single text node only until something splits them.
   A selection that has escaped this row reads as the very end, so Enter adds a row rather than
   cutting across two of them. */
function caretIn(words) {
  const chosen = getSelection();
  const spot = chosen.rangeCount ? chosen.getRangeAt(0) : null;
  const end = words.textContent.length;
  if (!spot || !words.contains(spot.startContainer) || !words.contains(spot.endContainer)) {
    return [end, end];
  }
  return [lengthBefore(words, spot.startContainer, spot.startOffset),
          lengthBefore(words, spot.endContainer, spot.endOffset)];
}

/* A section is one list, whether it is drawn as one or as an open list with a folded Done one
   beneath it. So the unit here is the <section>, not a <ul>: a save gathers every row under it,
   and an edit in either list writes the whole thing back. */
for (const section of document.querySelectorAll(".section")) {
  /* What the page believes the file holds, so a save can tell their own edit from an item Entity
     filed into the same section while the window sat open. It is what was last SENT rather than
     what was first drawn, because the file rewrites `- x` as `- [ ] x` the moment anything saves
     it, and a stale answer here files a second copy of everything they have edited since. */
  let drawn = itemsOf(section).map((item) => item.text);
  const save = async (leaving) => {
    const items = itemsOf(section);
    const was = drawn;
    drawn = items.map((item) => item.text);
    if (leaving) {
      return post("/profile", asJson({ heading: section.dataset.heading, items, drawn: was }), true);
    }
    const response = await fetch("/profile", {
      method: "POST", body: asJson({ heading: section.dataset.heading, items, drawn: was }),
    });
    announce("Saved");
    // The server hands each sent row its number; the pending tags become the real ids in place,
    // so a new item shows its number the moment it first saves rather than on the next page load.
    const { ids } = await response.json();
    rowsOf(section).forEach((row, at) => {
      if (!ids || ids[at] == null) return;
      row.dataset.id = ids[at];
      const tag = row.querySelector(".tag");
      if (tag) {
        tag.textContent = `#${ids[at]}`;
        tag.classList.remove("pending");
      }
    });
  };

  /* An item that gets done is ticked, never removed: it is the only record that a complaint was
     heard and acted on. Dimmed rather than struck through, so it stays legible. It joins the Done
     fold on the next draw of the page, not the instant it is ticked - moving it out from under the
     caret mid-click would be its own surprise. */
  section.addEventListener("change", (event) => {
    event.target.closest("li").classList.toggle("done", event.target.checked);
    atOnce(section, save);
  });

  section.addEventListener("input", () => soon(section, save));
  section.addEventListener("focusout", () => flush(section));

  /* A pasted block becomes one item per line. The first line joins whatever the caret was in; the
     rest become fresh rows after it, and the words that were to the right of the caret ride on the
     last one. Without this the browser drops a block into a single row and it smooshes into one
     note - and a plain-text paste can lose its line breaks entirely, which is why the lines are
     read off the clipboard here rather than out of the row afterwards. */
  section.addEventListener("paste", (event) => {
    const words = event.target.closest(".item");
    if (!words) return;
    const lines = pastedLines(event.clipboardData.getData("text/plain"));
    if (!lines.length) return;   // nothing but blank lines and markers - let the default no-op
    event.preventDefault();
    const said = words.textContent;
    const [from, to] = caretIn(words);
    words.textContent = said.slice(0, from) + lines[0];
    let anchor = words.closest("li");
    for (const line of lines.slice(1)) {
      const next = freshRow(anchor);
      wordsOf(next).textContent = line;
      anchor.after(next);
      anchor = next;
    }
    wordsOf(anchor).textContent += said.slice(to);
    soon(section, save);
    caretTo(anchor, false);
  });

  section.addEventListener("keydown", (event) => {
    const words = event.target.closest(".item");
    if (!words) return;
    const row = words.closest("li");
    if (event.key === "Enter") {
      /* Enter is how an item is made - the whole point of this page. Whatever is to the right of
         the caret goes with it, so Enter at the end of a line (which is where it is pressed) makes
         an empty one, and Enter in the middle of one splits it where they asked. */
      event.preventDefault();
      const said = words.textContent;
      const [from, to] = caretIn(words);
      words.textContent = said.slice(0, from);
      const next = freshRow(row);
      wordsOf(next).textContent = said.slice(to);
      row.after(next);
      soon(section, save);
      caretTo(next, true);                // ...which loses focus, and sends what was just made
    } else if (event.key === "Backspace" && !words.textContent
               && row.closest("ul").querySelectorAll("li").length > 1) {
      /* Backspace out of a row they made and did not fill in, the way they made it. Only an empty
         one: an item with words is removed by emptying it first, never by a stray keystroke. Never
         the last row of a list, so the open list always keeps a line to type into. */
      event.preventDefault();
      const above = row.previousElementSibling;
      const back = above || row.nextElementSibling;
      row.remove();
      soon(section, save);
      caretTo(back, !above);
    }
  });
}


/* ---- Ctrl+F: the page's own find --------------------------------------------------------- */

/* The embedded browser has no find bar, so the list pages carry their own: type, and every row
   whose words match lights up; Enter walks them; Escape puts it away. */
const finder = document.createElement("div");
finder.id = "finder";
finder.hidden = true;
const finderBox = document.createElement("input");
finderBox.type = "search";
finderBox.placeholder = "Find…";
const finderCount = document.createElement("span");
finderCount.className = "count";
finder.append(finderBox, finderCount);
document.body.append(finder);

let found = [];
let foundAt = -1;

function clearFound() {
  for (const row of document.querySelectorAll(".found, .found-here")) {
    row.classList.remove("found", "found-here");
  }
  found = [];
  foundAt = -1;
}

function findNow() {
  clearFound();
  const wanted = finderBox.value.trim().toLowerCase();
  if (!wanted) { finderCount.textContent = ""; return; }
  for (const row of document.querySelectorAll(".checklist li")) {
    if (row.textContent.toLowerCase().includes(wanted)) {
      row.classList.add("found");
      found.push(row);
    }
  }
  finderCount.textContent = found.length ? `${found.length}` : "0";
  if (found.length) visit(0);
}

function visit(at) {
  if (!found.length) return;
  found[foundAt]?.classList.remove("found-here");
  foundAt = (at + found.length) % found.length;
  const row = found[foundAt];
  row.classList.add("found-here");
  row.closest("details")?.setAttribute("open", "");  // a done match unfolds to be seen
  row.scrollIntoView({ block: "center" });
}

finderBox.addEventListener("input", findNow);
finderBox.addEventListener("keydown", (event) => {
  if (event.key === "Enter") { event.preventDefault(); visit(foundAt + 1); }
  if (event.key === "Escape") { finder.hidden = true; clearFound(); finderCount.textContent = ""; }
});

addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f") {
    event.preventDefault();
    finder.hidden = false;
    finderBox.focus();
    finderBox.select();
  }
});
