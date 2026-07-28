"""The restart's second half, as its own process: wait for the old app to die, start a new one.

The Restart button's relaunch used to be the OLD process's last act - which meant it only
happened if the wind-down reached that line, and the one time it mattered most (teardown
misbehaving, the very reason he pressed Restart) the app closed and nothing came back; he had
to reopen it by hand. Spawned as a detached helper at the moment restart is REQUESTED, this
outlives whatever the old process does: it waits for that pid to disappear - however the app
goes, clean or crashed - then launches a fresh one on the current code.

Run as: pythonw -m entity.relauncher <old-pid> <repo-root>
"""

import subprocess
import sys
import time
from pathlib import Path


def _alive(pid, kernel32):
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = __import__("ctypes").c_ulong()
        kernel32.GetExitCodeProcess(handle, __import__("ctypes").byref(code))
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def wait_then_launch(pid, repo, *, timeout=120.0, poll=0.5):
    import ctypes

    kernel32 = ctypes.windll.kernel32
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and _alive(pid, kernel32):
        time.sleep(poll)
    # Timed out or died - either way the old window is not coming back; bring up the new one.
    pythonw = Path(repo) / ".venv" / "Scripts" / "pythonw.exe"
    subprocess.Popen(
        [str(pythonw), "-m", "entity", "--gui"], cwd=str(repo),
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )


if __name__ == "__main__":
    wait_then_launch(int(sys.argv[1]), sys.argv[2])
