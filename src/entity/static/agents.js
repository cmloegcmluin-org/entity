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

/* Archived exchanges load once, on first open. They never grow, so there is nothing to poll -
   and pages with a long history would otherwise pay for every retired agent every second. */
for (const shelf of document.querySelectorAll("details.archived")) {
  shelf.addEventListener("toggle", async () => {
    if (!shelf.open || shelf.dataset.loaded) return;
    shelf.dataset.loaded = "1";
    const shown = await (await fetch(
      `/agents/archived/${encodeURIComponent(shelf.dataset.agent)}?since=0`)).json();
    drawInto(shelf.querySelector(".thread"), shown.entries, shown.at);
  });
}

/* Closing one archives its log and takes its section away. The archive is what makes it stick:
   the roster is the log folder, so a log left in place comes back on the next poll. */
for (const shut of document.querySelectorAll(".shut")) {
  shut.onclick = async () => {
    const name = shut.dataset.agent;
    await fetch(`/agents/${encodeURIComponent(name)}/close`, { method: "POST" });
    const gone = threads.findIndex((agent) => agent.where.dataset.agent === name);
    if (gone >= 0) threads.splice(gone, 1);
    shut.closest("section").remove();
  };
}
