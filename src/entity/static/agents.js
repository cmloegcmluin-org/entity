/* Every agent's exchange on one page, each tailed as it works. */

const threads = [...document.querySelectorAll(".agent")].map((where) => ({ where, drawn: 0 }));

async function follow() {
  for (const agent of threads) {
    const shown = await (await fetch(
      `/agents/${encodeURIComponent(agent.where.dataset.agent)}?since=${agent.drawn}`)).json();
    drawInto(agent.where, shown.entries, shown.at);
    agent.drawn = shown.total;
  }
}

if (threads.length) {
  follow();
  setInterval(follow, 1000);  // an agent writes as it works, but not four times a second
}

/* The rail: every log one click away. An active name scrolls its tab into view; an archived name
   UNARCHIVES the log - the reload redraws it as an ordinary tab - and the #hash carries which one
   to scroll to once it exists. The lists are server-drawn, so any change of membership (a close,
   a restore) reloads rather than patching the page by hand. */
for (const goes of document.querySelectorAll("#toc button[data-goes]")) {
  goes.addEventListener("click", () => {
    document.getElementById(goes.dataset.goes)?.scrollIntoView({ block: "start" });
  });
}

for (const shelf of document.querySelectorAll("#toc button[data-restore]")) {
  shelf.addEventListener("click", async () => {
    const name = shelf.dataset.restore;
    await fetch(`/agents/archived/${encodeURIComponent(name)}/restore`, { method: "POST" });
    location.assign(`/agents#agent-${encodeURIComponent(name)}`);
    location.reload();  // assign alone won't reload when only the hash differs
  });
}

if (location.hash) {
  // The restore lands here: the freshly unarchived tab exists now - bring it into view.
  document.getElementById(decodeURIComponent(location.hash.slice(1)))
    ?.scrollIntoView({ block: "start" });
}

/* Closing one archives its log, and the reload moves its name to the rail's Archived list - the
   archive is what makes the close stick: the roster is the log folder, so a log left in place
   comes back on the next poll. */
for (const shut of document.querySelectorAll(".shut")) {
  shut.onclick = async () => {
    const name = shut.dataset.agent;
    await fetch(`/agents/${encodeURIComponent(name)}/close`, { method: "POST" });
    location.assign("/agents");
    location.reload();
  };
}
