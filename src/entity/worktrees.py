"""Cutting and finding the git worktrees the Entity's agents work in.

New work means a NEW worktree, cut from CURRENT origin/main - never a stale local branch that
has fallen behind whatever else has merged. `run` is injected so the git calls can be exercised
without a real repo.
"""

import functools
import subprocess
from pathlib import Path

# From a windowed run there's no console for git to inherit, so each call would flash its own
# console window onto his monitors. Same git, no window.
_run_hidden = functools.partial(subprocess.run, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def is_worktree(path):
    """A directory with a .git inside - a file in a linked worktree, a directory in a main checkout."""
    return (Path(path).expanduser() / ".git").exists()


def find_worktrees(directory):
    """The worktrees directly inside `directory` - only real ones, recognized by their .git. A
    worktree's OWN subdirectories (.venv, docs, src...) have no .git and must never count: treating
    them as worktrees once put a separate agent to work inside each folder of a single worktree."""
    path = Path(directory).expanduser()
    if not path.is_dir():
        return []
    return sorted(str(child) for child in path.iterdir() if child.is_dir() and is_worktree(child))


def prepare_worktree(repo, path, branch, *, base="origin/main", remote="origin", run=_run_hidden):
    """Create a fresh worktree at `path` on a new `branch` cut from CURRENT `origin/main`.

    Fetch first, then branch from the just-fetched remote tip - so an agent never starts from stale
    local code that's fallen behind whatever else has merged. `run` is injected so the git calls can
    be exercised without a real repo.
    """
    run(["git", "-C", str(repo), "fetch", remote, "main"], check=True)
    run(["git", "-C", str(repo), "worktree", "add", "-b", branch, str(path), base], check=True)
    return str(path)


def prepare_worktree_for(path, *, run=_run_hidden):
    """Cut a fresh worktree for `path` from current origin/main, inferring which repo it belongs to.

    Starting brand-new worktrees is the norm, so this assumes nothing already exists: it finds the
    repo by asking git at `path`'s nearest existing ancestor (the leaf - and maybe the whole
    .claude/worktrees dir - don't exist yet), names the branch after the worktree, and hands off to
    `prepare_worktree`. `run` is injected so it's exercised without a real repo.
    """
    target = Path(path)
    anchor = target
    while not anchor.exists():
        anchor = anchor.parent
    repo = run(
        ["git", "-C", str(anchor), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return prepare_worktree(repo, target, f"claude/{target.name}", run=run)
