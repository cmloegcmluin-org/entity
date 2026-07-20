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
    # him WHICH file, which "that file" would not. The full stop after it is the sentence's.
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
