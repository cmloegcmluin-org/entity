from entity.models import DEFAULT_EFFORT, DEFAULT_MODEL, describe, resolve


def test_the_default_is_the_model_he_would_have_used_himself():
    # "Sonnet's not so hot either. I usually use Opus. It should default to Opus 4.8 on High."
    assert (DEFAULT_MODEL, DEFAULT_EFFORT) == ("claude-opus-4-8", "high")


def test_a_spoken_choice_becomes_a_model_and_an_effort():
    # "I should be able to ask it for Fable Max for example if I want" - said out loud, in that
    # order, as one phrase.
    assert resolve("Fable Max") == ("claude-fable-5", "max")
    assert resolve("put the agents on opus, high") == ("claude-opus-4-8", "high")


def test_either_half_can_be_left_out():
    # They says one or the other as often as both, and the half they didn't mention is not a reason to
    # ignore the half they did.
    assert resolve("use Fable") == ("claude-fable-5", None)
    assert resolve("crank it to max") == (None, "max")


def test_a_full_model_id_is_the_users_choice_too():
    assert resolve("claude-haiku-4-5-20251001") == ("claude-haiku-4-5-20251001", None)


def test_a_phrase_with_no_choice_in_it_resolves_to_nothing():
    # So the caller can say it didn't understand, rather than quietly running their work on whatever
    # a guess landed on.
    assert resolve("thanks, that's great") is None
    assert resolve("") is None


def test_the_choice_reads_back_in_the_words_he_used():
    assert describe("claude-fable-5", "max") == "Fable on max"
    assert describe("claude-opus-4-8", "high") == "Opus on high"
