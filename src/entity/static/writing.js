/* The pages that are edited in place: the profile's sections, and what Entity has learned.

   There is no Save button, because a document you have to remember to save is one you lose. It
   writes back when typing stops, and says so - a save nobody can see is one nobody trusts. */

const saved = document.getElementById("saved");
const AFTER = 1200;   // milliseconds of not typing before what is written goes back

function announce(what) {
  saved.textContent = what;
  saved.classList.add("showing");
  setTimeout(() => saved.classList.remove("showing"), 1600);
}

async function write(where, body) {
  await fetch(where, { method: "POST", body: new URLSearchParams(body) });
  announce("Saved");
}

/* Where a box writes itself back to, and what it has to say to be understood there. */
function destination(box) {
  if (box.dataset.learned) return ["/memory", { body: box.value }];
  if (box.dataset.translations) return ["/translations", { body: box.value }];
  return ["/profile", { heading: box.dataset.heading, body: box.value,
                        checklist: box.dataset.checklist || "false" }];
}

for (const box of document.querySelectorAll(".writing")) {
  let waiting = null;
  box.addEventListener("input", () => {
    clearTimeout(waiting);
    waiting = setTimeout(() => write(...destination(box)), AFTER);
  });
}

/* A checklist writes back the whole list every time one box is ticked: an item that gets done is
   ticked, never removed, so what is stored is the list with one mark changed. */
for (const list of document.querySelectorAll(".checklist")) {
  list.addEventListener("change", (event) => {
    if (event.target.type !== "checkbox") return;
    event.target.closest("li").classList.toggle("done", event.target.checked);
    const body = [...list.querySelectorAll("li")].map((row) => {
      const ticked = row.querySelector("input").checked;
      return `${ticked ? "☑" : "☐"} ${row.querySelector("span").textContent}`;
    }).join("\n");
    write("/profile", { heading: list.dataset.heading, body, checklist: "true" });
  });
}
