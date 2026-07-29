"""The cards' command-line door: the same savers the app uses, minus the need for the app."""

import pytest

from entity.cards import main, without_rows


def test_rows_named_by_unique_fragment_are_dropped_and_the_rest_stand():
    kept = without_rows("- keep me" + chr(10) + "- drop me please" + chr(10) + "- also keep",
                        ["drop me"])
    assert kept == "- keep me" + chr(10) + "- also keep"


def test_a_fragment_matching_zero_or_many_rows_refuses_the_whole_edit():
    rows = "- alpha one" + chr(10) + "- alpha two"
    with pytest.raises(SystemExit):
        without_rows(rows, ["alpha"])  # two hits: refuse rather than guess
    with pytest.raises(SystemExit):
        without_rows(rows, ["beta"])  # zero hits: same refusal
    with pytest.raises(SystemExit):
        without_rows(rows, ["alpha one", "gamma"])  # one bad fragment poisons the run


def test_tick_checks_an_enhancement_off_by_number(tmp_path, monkeypatch):
    from entity import cards, memory

    profile = tmp_path / "profile.md"
    profile.write_text("## Enhancements he wants (roadmap, not now)" + chr(10)
                       + "- [ ] #7 louder voice" + chr(10), encoding="utf-8")
    monkeypatch.setattr(memory, "DEFAULT_PROFILE_PATH", profile)
    monkeypatch.setattr(cards, "complete_enhancement_by_id",
                        lambda item_id: memory.complete_enhancement_by_id(item_id, path=profile))

    assert main(["tick", "7"]) == 0
    assert "- [x] #7 louder voice" in profile.read_text(encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["tick", "99"])  # no such item: say so, change nothing


def test_drop_instruction_rewrites_the_card_through_its_own_saver(tmp_path, monkeypatch):
    from entity import cards

    card = tmp_path / "persona.md"
    card.write_text("- first rule" + chr(10) + "- second rule" + chr(10), encoding="utf-8")
    monkeypatch.setattr(cards, "load_persona_additions",
                        lambda: card.read_text(encoding="utf-8"))
    monkeypatch.setattr(cards, "save_persona_additions",
                        lambda text, path: card.write_text(text.rstrip() + chr(10),
                                                           encoding="utf-8"))

    assert main(["drop-instruction", "second rule"]) == 0
    assert card.read_text(encoding="utf-8") == "- first rule" + chr(10)
