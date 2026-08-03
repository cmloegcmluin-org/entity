from excephalon.tailing import LogTail, archive_dir, discover


def test_discover_lists_log_files_by_name(tmp_path):
    (tmp_path / "fixer.log").write_text("x", encoding="utf-8")
    (tmp_path / "helper.log").write_text("y", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("z", encoding="utf-8")  # not a log - not a tab

    assert discover(tmp_path) == ["fixer", "helper"]


def test_the_archive_is_a_sibling_of_the_live_folder_so_discover_stays_clear_of_it(tmp_path):
    # The one rule both the desk and the window archive by: an archived log lands OUTSIDE the
    # folder discover globs, so it never comes back as a tab. A subfolder would be re-discovered
    # on the next poll if discover ever recursed; a sibling never can be.
    live = tmp_path / "agent-logs"
    live.mkdir()
    archive = archive_dir(live)

    assert archive == tmp_path / "agent-logs-archive"
    assert archive.parent == live.parent and archive not in live.parents


def test_discover_survives_a_directory_that_does_not_exist_yet(tmp_path):
    assert discover(tmp_path / "nowhere") == []


def test_a_tail_yields_only_what_is_new_since_last_poll(tmp_path):
    path = tmp_path / "fixer.log"
    path.write_text("first line\n", encoding="utf-8")
    tail = LogTail(path)

    assert tail.poll() == "first line\n"
    assert tail.poll() == ""  # nothing new

    with open(path, "a", encoding="utf-8") as handle:
        handle.write("second line\n")

    assert tail.poll() == "second line\n"


def test_a_tail_of_a_vanished_file_yields_nothing_rather_than_raising(tmp_path):
    path = tmp_path / "fixer.log"
    path.write_text("here\n", encoding="utf-8")
    tail = LogTail(path)
    tail.poll()
    path.unlink()

    assert tail.poll() == ""
