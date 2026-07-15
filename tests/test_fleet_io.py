from entity.fleet_io import VoiceFleetIO, _choice_from, _is_yes


def test_is_yes_reads_approval_words():
    assert _is_yes("yes go ahead")
    assert _is_yes("yeah do it")
    assert _is_yes("approve")
    assert not _is_yes("no, not that one")
    assert not _is_yes("hold off")


def test_choice_from_matches_a_digit():
    names = ["the-tracker-note", "voice-fallback", "bin-buttons"]
    assert _choice_from("number two", names) == "voice-fallback"
    assert _choice_from("3", names) == "bin-buttons"


def test_choice_from_matches_a_number_word_or_name_fragment():
    names = ["the-tracker-note", "voice-fallback"]
    assert _choice_from("the first one", names) == "the-tracker-note"
    assert _choice_from("do the voice one", names) == "voice-fallback"


def test_choice_from_returns_none_when_unrecognized():
    assert _choice_from("uhh what", ["a", "b"]) is None


def test_voice_io_asks_for_the_number_and_relays_the_decision():
    spoken = []
    heard = iter(["number two", "yes do it"])
    io = VoiceFleetIO(speak=spoken.append, listen=lambda: next(heard))

    picked = io.pick(["the-tracker-note", "voice-fallback"])
    approved = io.approve("voice-fallback", "run: npm test")

    assert picked == "voice-fallback"
    assert approved is True
    assert any("Say the number" in line for line in spoken)
    assert any("Yes or no" in line for line in spoken)
