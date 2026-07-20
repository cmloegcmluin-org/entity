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
