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
