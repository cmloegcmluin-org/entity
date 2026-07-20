from entity.bubbles import hold_back, link_runs, size_bubble, wrap_to_pixels


def _measure(text):
    """A fake font: every character is 10px wide, so the maths is readable in the assertions."""
    return len(text) * 10


def test_a_long_message_breaks_into_lines_that_fit_the_bubble():
    assert wrap_to_pixels("one two three four", 80, _measure) == ["one two", "three", "four"]


def test_the_lines_he_typed_stay_lines_of_their_own():
    assert wrap_to_pixels("eggs\nmilk", 500, _measure) == ["eggs", "milk"]


def test_a_word_too_long_for_the_bubble_is_broken_rather_than_left_hanging_out():
    # A pasted URL must not push the bubble past its half of the pane, and must not be clipped
    # away either - so it breaks mid-word.
    assert wrap_to_pixels("see http://example.com/x", 80, _measure) == [
        "see", "http://e", "xample.c", "om/x",
    ]


def test_a_path_is_found_where_the_wrapping_actually_put_it():
    # Not where it sits in the message: wrapping joins words with single spaces and moves them
    # onto lines of its own, so a search of the message would underline the wrong characters.
    lines = wrap_to_pixels("I put it in C:\\ada\\task.md just now", 200, _measure)

    assert lines == ["I put it in", "C:\\ada\\task.md just", "now"]
    assert link_runs("I put it in C:\\ada\\task.md just now", lines) == [
        ("C:\\ada\\task.md", [(1, 0, 14)]),
    ]


def test_a_link_too_long_for_the_bubble_stays_one_link_across_the_pieces_it_was_cut_into():
    # A bubble is capped at its share of the pane, so a long address is cut mid-word to fit. Each
    # piece has to open the WHOLE address: underlining one of them alone would offer "http://e".
    lines = wrap_to_pixels("see http://example.com/x", 80, _measure)

    assert lines == ["see", "http://e", "xample.c", "om/x"]
    assert link_runs("see http://example.com/x", lines) == [
        ("http://example.com/x", [(1, 0, 8), (2, 0, 8), (3, 0, 4)]),
    ]


def test_the_sentence_around_a_link_is_not_part_of_it():
    lines = wrap_to_pixels("(https://example.com/a) and C:\\ada\\x.md.", 500, _measure)

    assert lines == ["(https://example.com/a) and C:\\ada\\x.md."]
    assert link_runs("(https://example.com/a) and C:\\ada\\x.md.", lines) == [
        ("https://example.com/a", [(0, 1, 21)]),  # not the bracket it sits in
        ("C:\\ada\\x.md", [(0, 28, 11)]),  # nor the full stop that ends the sentence
    ]


def test_a_short_message_gets_a_bubble_only_as_wide_as_its_words():
    lines, width, _ = size_bubble("hey", 1000, _measure, 20)

    assert lines == ["hey"]
    assert width == 50  # three characters and its padding - not a stripe across the pane


def test_a_long_message_stops_at_its_share_of_the_pane_with_the_padding_inside_it():
    lines, width, height = size_bubble("x" * 200, 1000, _measure, 20)

    assert width == 550  # 55% of a 1000px pane, padding counted in - never edge to edge
    assert len(lines) == 4 and height == 4 * 20 + 12


def test_every_session_ever_is_held_and_only_its_newest_page_is_built():
    # It opens on the live end of the thread; the rest is built as it is scrolled back to, because
    # a bubble apiece for the whole archive costs a second a thousand and the archive only grows.
    waiting, building = hold_back(list(range(100)), already=0, page=40)

    assert building == list(range(60, 100))
    assert waiting == list(range(60))


def test_a_message_arriving_later_is_built_where_it_belongs_at_the_bottom():
    # Only the opening batch is ever held back. A later one is newer than everything already up,
    # so holding it would mean prepending it above messages it came after.
    assert hold_back(list(range(100)), already=40, page=40) == ([], list(range(100)))
