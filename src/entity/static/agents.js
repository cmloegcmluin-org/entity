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
for (const goes of document.querySelectorAll("#toc [data-goes]")) {
  goes.addEventListener("click", () => {
    document.getElementById(goes.dataset.goes)?.scrollIntoView({ block: "start" });
  });
}

for (const shelf of document.querySelectorAll("#toc [data-restore]")) {
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

/* The rail's archive button does what the tab's ✕ does, from the list rather than the tab. */
for (const put of document.querySelectorAll("#toc [data-archive]")) {
  put.addEventListener("click", async () => {
    await fetch(`/agents/${encodeURIComponent(put.dataset.archive)}/close`, { method: "POST" });
    location.assign("/agents");
    location.reload();
  });
}

/* The name is his. Typing in a tab's heading and leaving it (or pressing Enter) saves it: the
   desk moves the log, re-keys its own record and re-tags any news waiting to be spoken, so the
   reload finds every mention of that agent under the new name. Escape puts back what was there. */
for (const heading of document.querySelectorAll(".rename")) {
  const was = heading.textContent.trim();
  const save = async () => {
    const wanted = heading.textContent.trim();
    if (!wanted || wanted === was) { heading.textContent = was; return; }
    const answer = await fetch(`/agents/${encodeURIComponent(was)}/rename`,
                               { method: "POST", body: new URLSearchParams({ to: wanted }) });
    if (!answer.ok) { heading.textContent = was; return; }
    location.assign("/agents");
    location.reload();
  };
  heading.addEventListener("blur", save);
  heading.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); heading.blur(); }
    if (event.key === "Escape") { heading.textContent = was; heading.blur(); }
  });
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
