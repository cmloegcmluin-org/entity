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


def test_projects_are_the_git_repos_directly_under_the_workspace(tmp_path):
    # "you should certainly come out of the gates knowing where my projects live." The brain had
    # to ask where a repo was; the workspace's own directory listing already knew.
    for name in ("highdeas", "entity"):
        (tmp_path / name / ".git").mkdir(parents=True)
    (tmp_path / "notes").mkdir()  # no .git - papers, not a project

    from entity.worktrees import projects

    assert projects(tmp_path) == ["entity", "highdeas"]


def test_no_workspace_means_no_projects(tmp_path):
    from entity.worktrees import projects

    assert projects(tmp_path / "nowhere") == []


def test_head_commit_reads_the_checkout_without_a_subprocess(tmp_path):
    # The Restart button compares the disk's commit against the booted one on a poll, so this
    # has to be file reads - and a repo it cannot read answers "", which callers treat as
    # "don't know", never as "changed".
    from entity.worktrees import head_commit

    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git / "refs" / "heads" / "main").write_text("abc123\n", encoding="utf-8")
    assert head_commit(tmp_path) == "abc123"

    (git / "refs" / "heads" / "main").unlink()
    (git / "packed-refs").write_text("# pack-refs\nabc999 refs/heads/main\n", encoding="utf-8")
    assert head_commit(tmp_path) == "abc999"

    (git / "HEAD").write_text("deadbeef\n", encoding="utf-8")  # detached
    assert head_commit(tmp_path) == "deadbeef"

    assert head_commit(tmp_path / "nowhere") == ""
