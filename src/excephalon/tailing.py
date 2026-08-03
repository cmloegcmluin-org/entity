"""Following the agents' log files as they grow - what feeds the window's agent tabs.

The desk writes runtime/agent-logs/<name>.log as it talks to each agent; the window shows one tab
per file and appends whatever is new on each poll. Byte-offset tailing, same as the inbox watcher:
append-only files, no OS watchers, nothing to go wrong across threads.
"""

import re
from pathlib import Path


def discover(directory):
    """The agent names with logs in `directory` - one window tab each."""
    path = Path(directory)
    if not path.is_dir():
        return []
    return sorted(child.stem for child in path.glob("*.log"))


def safe_name(wanted):
    """`wanted` as a name a log file can carry, or "" if nothing usable is left.

    An agent's name IS a filename (its log) and a URL segment (its tab), so his own words are
    trimmed to what both can hold - letters, digits, dashes - rather than refused outright for a
    space or a capital he would reasonably type.
    """
    kept = re.sub(r"[^A-Za-z0-9]+", "-", str(wanted).strip()).strip("-")
    return kept[:60]


def archive_dir(live_dir):
    """Where a finished agent's log goes to rest: runtime/agent-logs-archive/, a SIBLING of the
    live folder rather than a subfolder of it, so `discover` (which globs the live folder) can
    never turn an archived log back into a window tab. Defined in one place so the desk's own
    wrap-up (`retire`) and the window's close button always send a log to the same archive - two
    call sites naming the folder themselves is how they drift and a fleet ends up with its history
    split across two directories."""
    return Path(live_dir).parent / "agent-logs-archive"


class LogTail:
    def __init__(self, path):
        self._path = Path(path)
        self._offset = 0

    def poll(self):
        """Whatever the file gained since last time, or "" (including when it's gone)."""
        try:
            with open(self._path, "r", encoding="utf-8", errors="replace") as handle:
                handle.seek(self._offset)
                new = handle.read()
                self._offset = handle.tell()
                return new
        except OSError:
            return ""
