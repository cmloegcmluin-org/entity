/* Ctrl+F: the page's own find, on every page.

   The embedded browser has no find bar of its own, so each page carries one: type, and everything
   whose words match lights up; Enter walks the matches, Escape puts it away. It began on the
   Config page and stayed there, which left the two pages with the most to search through - the
   conversation and the agents' exchanges - with no way to search at all.

   What counts as one "row" differs per page, so the selector names all of them and whichever
   exists is what gets searched: a checklist item, a message in a thread, a row in the rail. */
const FINDABLE = ".checklist li, #thread .said, #thread .aside, .agent.thread .said," +
                 " .agent.thread .aside, #toc .rail-row, .section h2";

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
  for (const row of document.querySelectorAll(FINDABLE)) {
    if (row.textContent.toLowerCase().includes(wanted)) {
      row.classList.add("found");
      found.push(row);
    }
  }
  finderCount.textContent = `${found.length}`;
  if (found.length) visit(0);
}

function visit(at) {
  if (!found.length) return;
  found[foundAt]?.classList.remove("found-here");
  foundAt = (at + found.length) % found.length;
  const row = found[foundAt];
  row.classList.add("found-here");
  row.closest("details")?.setAttribute("open", "");  // a folded match unfolds to be seen
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
