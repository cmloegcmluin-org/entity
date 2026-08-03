from excephalon.hesitation import without_hesitations


def test_the_sounds_he_makes_while_thinking_are_dropped():
    assert without_hesitations("Um, I think uh we should ship it") == "I think we should ship it"
    assert without_hesitations("Uhh so uhm yeah") == "So yeah"  # the capital carries on


def test_a_dropped_opener_hands_its_capital_to_the_word_left_standing():
    assert without_hesitations("Um, the drive link is wrong") == "The drive link is wrong"
    assert without_hesitations("Right. Uh, ship it.") == "Right. Ship it."


def test_real_words_that_merely_start_that_way_are_kept():
    assert without_hesitations("an umbrella, uh, in the hall") == "an umbrella, in the hall"
    assert without_hesitations("uhm humble uh") == "Humble"


def test_a_turn_that_was_nothing_but_hesitation_comes_out_empty():
    assert without_hesitations("Um. Uh.") == ""
    assert without_hesitations("") == ""
