from entity.relay import notice


def test_an_agents_report_reaches_him_as_a_notice_not_as_its_own_words():
    report = (
        "DONE. Google Drive per-memo folder links complete, tested, committed. Independently "
        "re-verified everything below myself just now, not just trusting the other instances. "
        "363 passed, 0 failed. Committed as 91459e5."
    )

    said = notice("hungry-neumann", report)

    assert said.startswith("hungry-neumann: DONE.")
    assert "91459e5" not in said and "363 passed" not in said  # its internals stay in its tab
    assert "tab" in said  # and he's told where the rest is


def test_a_short_report_arrives_whole_with_nothing_to_point_at():
    assert notice("fixer", "Tests are green; needs your Cloud steps.") == (
        "fixer: Tests are green; needs your Cloud steps."
    )


def test_a_single_enormous_sentence_is_still_cut_short():
    said = notice("fixer", "and then " * 200)

    assert len(said) < 200
    assert said.endswith("tab)")


def test_an_empty_report_says_so_rather_than_saying_nothing():
    assert "empty" in notice("fixer", "   ")
