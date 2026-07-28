/* The window's own close question, and the restart button - the two ways this window ends.

   The native confirm was a light-mode system box inside a dark app, so it is off: the window's
   closing event calls askToClose() here and cancels itself, and only the dialog's Close button -
   through POST /quit, which the server answers by destroying the window - actually closes. */

const veil = document.getElementById("veil");
const keep = document.getElementById("keep-open");

/* Called by the app when the X is pressed (see desktop.Controls.asked_to_close). */
function askToClose() {
  veil.hidden = false;
  keep.focus();
}

keep.addEventListener("click", () => { veil.hidden = true; });
addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !veil.hidden) veil.hidden = true;
});
veil.addEventListener("pointerdown", (event) => {
  if (event.target === veil) veil.hidden = true;   // a click off the dialog keeps the window
});

document.getElementById("really-close").addEventListener("click", () => {
  fetch("/quit", { method: "POST" });
});

/* One click from a landed fix to running it. The process winds down exactly as a close does -
   goodbye, agents recorded - and its last act is to start a fresh one on the current code. */
document.getElementById("restart").addEventListener("click", () => {
  fetch("/restart", { method: "POST" });
});
