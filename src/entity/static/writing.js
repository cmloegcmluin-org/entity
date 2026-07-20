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

for (const box of document.querySelectorAll(".writing")) {
  let waiting = null;
  box.addEventListener("input", () => {
    clearTimeout(waiting);
    waiting = setTimeout(async () => {
      const where = box.dataset.learned ? "/memory" : "/profile";
      const body = { body: box.value };
      if (!box.dataset.learned) body.heading = box.dataset.heading;
      await fetch(where, { method: "POST", body: new URLSearchParams(body) });
      announce("Saved");
    }, AFTER);
  });
}
