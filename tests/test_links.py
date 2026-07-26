from entity.links import as_spoken, link_in, open_link


def test_a_web_address_and_a_path_on_this_machine_are_both_openable():
    assert link_in("https://example.com/notes") == "https://example.com/notes"
    assert link_in(r"C:\Users\ada\workspace\runtime\inbox\task.md") == \
        r"C:\Users\ada\workspace\runtime\inbox\task.md"
    assert link_in("ordinary") is None


def test_the_sentence_keeps_its_own_punctuation():
    # Entity writes these mid-sentence, so the full stop after a filename is the sentence's and
    # opening "notes.md." opens nothing.
    assert link_in(r"C:\ada\notes.md.") == r"C:\ada\notes.md"
    assert link_in("(https://example.com/a)") == "https://example.com/a"
    assert link_in("https://example.com/a,") == "https://example.com/a"


def test_a_web_address_is_not_read_out_aloud():
    # Nobody says "aitch tee tee pee colon slash slash" - they say "the link" and expect the thing
    # itself to be on the screen, which it is: only what is SPOKEN is stood in for.
    assert as_spoken("It's at https://example.com/a/b?c=d if you want it.") == \
        "It's at the link if you want it."


def test_a_path_is_said_the_way_a_person_says_it_and_the_sentence_keeps_its_punctuation():
    # The last part is what anyone would actually say - "it's in profile.md" - and it still tells
    # them WHICH file, which "that file" would not. The full stop after it is the sentence's.
    assert as_spoken(r"I put it in C:\Users\ada\workspace\entity\runtime\profile.md.") == \
        "I put it in profile.md."
    assert as_spoken(r"Look in (C:\Users\ada\workspace\entity) for it.") == "Look in (entity) for it."


def test_an_address_goes_to_the_browser_and_a_file_to_the_machine(tmp_path):
    note = tmp_path / "task.md"
    note.write_text("something", encoding="utf-8")
    browsed, opened = [], []

    open_link("https://example.com/a", browser=browsed.append, shell=opened.append)
    open_link(str(note), browser=browsed.append, shell=opened.append)

    assert browsed == ["https://example.com/a"]
    assert opened == [str(note)]


def test_a_file_not_written_yet_opens_the_nearest_folder_that_is_there(tmp_path):
    # Entity names a file in the same breath as making it, and a click landing a moment early
    # would open nothing at all - which reads as the window being broken rather than as being
    # early. The folder it is going into is somewhere to look.
    inbox = tmp_path / "runtime" / "agent-inbox"
    inbox.mkdir(parents=True)
    opened = []

    open_link(str(inbox / "not-yet.md"), shell=opened.append)

    assert opened == [str(inbox)]


def test_a_path_with_a_space_is_one_link_when_the_disk_confirms_it():
    from entity.links import link_parts

    # "C:\Users\ada\Field Notes\inbox." broke on the space and came out a broken link.
    # The filesystem settles it: the run that exists is the whole path, folder-with-a-space and all.
    real = r"C:\Users\ada\Field Notes\inbox"
    parts = link_parts(f"It's in {real}.", exists=lambda p: p == real)

    assert [p["link"] for p in parts if p["link"]] == [real]
    # Not one word lost, and the "." stays the sentence's, outside the link. (Words rejoin on
    # single spaces, so the reconstruction can carry one trailing space the box never shows.)
    assert "".join(p["text"] for p in parts).rstrip() == f"It's in {real}."


def test_a_real_word_after_a_real_path_is_not_swallowed_into_it():
    from entity.links import link_parts

    here = r"C:\ada\notes"
    parts = link_parts(f"{here} and then lunch", exists=lambda p: p == here)

    assert [p["link"] for p in parts if p["link"]] == [here]  # only the path, not "notes and then"


def test_a_path_that_exists_nowhere_is_still_the_one_word_it_was():
    from entity.links import link_parts

    parts = link_parts(r"See C:\ada\gone.md now", exists=lambda p: False)

    # Entity names a file a moment before making it, so a single-token path is offered regardless;
    # only EXTENDING across a space needs the disk.
    assert [p["link"] for p in parts if p["link"]] == [r"C:\ada\gone.md"]


def test_only_what_the_module_would_offer_can_be_opened():
    from entity.links import offers

    real = r"C:\Users\ada\Field Notes\inbox"
    assert offers("https://ex.com/x") is True
    assert offers(real, exists=lambda p: p == real) is True  # the spaced path it just handed out
    assert offers("not a link at all") is False
    assert offers(real, exists=lambda p: False) is False  # a spaced path that exists nowhere


def test_a_bare_localhost_address_is_a_link():
    # "when it said localhost:5200 I couldn't click it" - the natural way a person writes a local
    # app's address has no scheme, and the renderer must still know it opens.
    assert link_in("localhost:5200") == "localhost:5200"
    assert link_in("127.0.0.1:5201/inbox") == "127.0.0.1:5201/inbox"
    assert link_in("localhost:5200.") == "localhost:5200"  # the sentence's own full stop


def test_ordinary_words_with_colons_are_not_links():
    assert link_in("note:") is None
    assert link_in("10:30") is None  # a time is not a port


def test_a_bare_localhost_address_opens_in_the_browser_with_its_scheme_restored():
    opened = []
    open_link("localhost:5200", browser=opened.append, shell=lambda where: None)

    assert opened == ["http://localhost:5200"]


def test_a_bare_localhost_address_is_spoken_as_written():
    # He liked hearing "localhost 5200" - it IS the natural spoken form, unlike a full URL.
    assert as_spoken("It's live at localhost:5200 now.") == "It's live at localhost:5200 now."
