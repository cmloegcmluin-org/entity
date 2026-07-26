from entity.outbox import News
from entity.waiting import chosen, roll_call


def _three():
    return [News("fixer: the drive link is fixed", about="fixer"),
            News("docs-sidebar: needs your call on the width", about="docs-sidebar"),
            News("drive-export: green and pushed", about="drive-export")]


def test_several_ready_at_once_are_read_out_numbered():
    # "when several are ready, tell them which and let them choose the order." Joined into one
    # utterance they arrive as a wall with no way to take them one at a time.
    news = [News("fixer: the drive link is fixed", about="fixer"),
            News("docs-sidebar: needs your call on the width", about="docs-sidebar")]

    assert roll_call(news) == "Two updates waiting. One, fixer. Two, docs-sidebar. Which first?"


def test_the_last_one_left_is_named_rather_than_numbered():
    # Numbering a list of one, and then asking which of it they want, reads as a machine reciting
    # a form. It still has to be SAID, though: unread news that goes quiet is news they never get.
    news = [News("fixer: the drive link is fixed", about="fixer")]

    assert roll_call(news) == "Still waiting: fixer."


def test_a_number_picks_the_one_at_that_place():
    # The whole reason they are numbered: an agent is named after its worktree, and no
    # speech-to-text spells `export-report-as-csv` back. A number always survives the trip.
    assert chosen("two", _three()) == 1
    assert chosen("number three", _three()) == 2
    assert chosen("1", _three()) == 0


def test_a_word_of_an_agents_name_picks_it_and_beats_a_number_word_in_the_same_sentence():
    # "the drive one" carries the word "one", and reading that as the number would answer about a
    # different agent while sounding exactly as though it had understood.
    assert chosen("the drive one", _three()) == 2
    assert chosen("sidebar", _three()) == 1


def test_a_sentence_that_merely_mentions_an_agent_is_not_a_pick():
    # Picking from a list is terse. Anything longer is them talking, and reading a notice at them
    # instead of answering would lose the turn - which is the failure the whole app is built round.
    heard = "what does the sidebar look like now that the new layout has landed"

    assert chosen(heard, _three()) is None


def test_a_word_two_of_them_share_picks_neither():
    # Worktrees cut from the same feature share most of their name. Answering about whichever came
    # first would sound certain and be wrong half the time; the roll call stands and they can say
    # a number instead.
    news = [News("a", about="sidebar-export"), News("b", about="sidebar-import")]

    assert chosen("the sidebar one", news) is None


def test_words_that_name_none_of_them_are_not_a_pick():
    assert chosen("what time is it", _three()) is None
    assert chosen("", _three()) is None
    assert chosen("five", _three()) is None  # a number past the end of the list names nothing
