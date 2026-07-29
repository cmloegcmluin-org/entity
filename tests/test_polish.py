from entity.polish import mend


def test_a_period_before_a_lowercase_continuation_is_healed():
    # His exact report: "what we need to do. in order for you to actually follow instructions"
    # went through untouched by the model-based repairman - which is retired; this is instant.
    raw = ("Please answer the question because it's important for me to understand what we "
           "need to do. in order for you to actually follow instructions.")

    assert mend(raw) == ("Please answer the question because it's important for me to "
                         "understand what we need to do in order for you to actually follow "
                         "instructions.")


def test_question_marks_and_stacked_marks_heal_the_same_way():
    assert mend("should it go over? to the other side") == "should it go over to the other side"
    assert mend("it stopped.. mid thought") == "it stopped mid thought"


def test_a_break_before_a_capital_is_left_alone():
    # "That Instead of creating" cannot be told from a real sentence boundary without
    # semantics, and the price of guessing is eating his meaning - so no guess is made.
    raw = "Send it to Asana. That is the whole ask."

    assert mend(raw) == raw


def test_abbreviation_like_dots_before_capitals_survive():
    assert mend("Ship v1.2 Tomorrow") == "Ship v1.2 Tomorrow"


def test_nothing_else_is_ever_touched():
    raw = "One sentence. Another sentence! A question? Yes."

    assert mend(raw) == raw


def test_a_clause_opener_after_a_full_stop_is_the_same_chop_wearing_a_capital():
    # Every chop he brought back was this shape. His three, verbatim, with what he said each
    # should have been - the joins this can decide, and only those.
    assert mend("We're just going to take advantage of the fact that the feature is already in "
                "the app. At least your best attempt at the feature.") == (
        "We're just going to take advantage of the fact that the feature is already in the app, "
        "at least your best attempt at the feature.")
    assert mend("You can give me the update first. Although I'm kind of surprised you have an "
                "update for that one. Because that feature is already done.") == (
        "You can give me the update first, although I'm kind of surprised you have an update for "
        "that one because that feature is already done.")
    assert mend("You're supposed to go out and do it. With a Claude agent.") == (
        "You're supposed to go out and do it with a Claude agent.")


def test_a_capital_that_is_not_a_clause_opener_keeps_its_sentence():
    # "...on anything other than yourself. You're supposed to..." reads exactly like a real
    # boundary, and so does every other sentence starting with a pronoun or a noun. Joining
    # those on a guess would run whole paragraphs of his together.
    assert mend("Please also review the recent interaction. It's making a ton of mistakes.") == (
        "Please also review the recent interaction. It's making a ton of mistakes.")
    assert mend("The demo is good to ship. Excephalon closes properly now.") == (
        "The demo is good to ship. Excephalon closes properly now.")


def test_an_opener_inside_a_sentence_is_left_where_it_is():
    # Only a chop is mended: the same words mid-sentence are his, and untouched.
    assert mend("I'll do it because you asked, and so will they.") == (
        "I'll do it because you asked, and so will they.")
