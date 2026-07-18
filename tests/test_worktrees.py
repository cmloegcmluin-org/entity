from types import SimpleNamespace

from entity.worktrees import prepare_worktree, prepare_worktree_for


def test_prepare_worktree_fetches_before_branching_from_current_origin_main():
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))

    returned = prepare_worktree(
        "/repo", "/repo/.claude/worktrees/new-agent", "claude/new-agent", run=fake_run
    )

    assert calls[0][0] == ["git", "-C", "/repo", "fetch", "origin", "main"]
    assert calls[1][0] == [
        "git", "-C", "/repo", "worktree", "add", "-b",
        "claude/new-agent", "/repo/.claude/worktrees/new-agent", "origin/main",
    ]
    assert all(kwargs.get("check") for _, kwargs in calls)  # a git failure must raise, not slip by
    assert returned == "/repo/.claude/worktrees/new-agent"


def test_prepare_worktree_for_infers_the_repo_and_branch_then_cuts_fresh(tmp_path):
    worktrees = tmp_path / ".claude" / "worktrees"
    worktrees.mkdir(parents=True)  # exists; the new leaf below does not yet
    new = worktrees / "brave-swan"
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(stdout=f"{tmp_path}\n")  # git rev-parse answers with the repo root

    returned = prepare_worktree_for(str(new), run=fake_run)

    assert calls[0] == ["git", "-C", str(worktrees), "rev-parse", "--show-toplevel"]
    assert ["git", "-C", str(tmp_path), "fetch", "origin", "main"] in calls
    assert [
        "git", "-C", str(tmp_path), "worktree", "add", "-b", "claude/brave-swan", str(new), "origin/main"
    ] in calls
    assert returned == str(new)


def test_prepare_worktree_for_walks_up_to_the_first_existing_ancestor(tmp_path):
    # The very first worktree in a repo: neither the leaf nor .claude/worktrees exists, so the repo
    # root itself is where we ask git which repo this is.
    new = tmp_path / ".claude" / "worktrees" / "calm-lake"
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        return SimpleNamespace(stdout=f"{tmp_path}\n")

    prepare_worktree_for(str(new), run=fake_run)

    assert seen[0] == ["git", "-C", str(tmp_path), "rev-parse", "--show-toplevel"]
