"""Following the agents' log files as they grow - what feeds the window's agent tabs.

The desk writes runtime/agent-logs/<name>.log as it talks to each agent; the window shows one tab
per file and appends whatever is new on each poll. Byte-offset tailing, same as the inbox watcher:
append-only files, no OS watchers, nothing to go wrong across threads.
"""

from pathlib import Path


def discover(directory):
    """The agent names with logs in `directory` - one window tab each."""
    path = Path(directory)
    if not path.is_dir():
        return []
    return sorted(child.stem for child in path.glob("*.log"))


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
