from excephalon.vocabulary import DEFAULT_TRANSLATIONS, correct_terms, scan_terms


def test_a_near_miss_token_is_corrected_to_the_known_term():
    # Parakeet misheard a coined word; the closest known term above the bar wins.
    assert correct_terms("open note craft", ["Notecraft"]) == "open note craft"
    assert correct_terms("open notcraft", ["Notecraft"]) == "open Notecraft"


def test_surrounding_punctuation_is_preserved_and_matched_on_the_bare_word():
    # the period must not drag the similarity down, and it must survive the swap.
    assert correct_terms("go to notcraft.", ["Notecraft"]) == "go to Notecraft."
    assert correct_terms("(waveshaper)", ["WaveShaper"]) == "(WaveShaper)"


def test_an_exact_word_is_recased_to_the_canonical_spelling():
    # Parakeet got the sounds right but not the capitalisation; hand back the known spelling.
    assert correct_terms("open notecraft now", ["Notecraft"]) == "open Notecraft now"
    assert correct_terms("Notecraft", ["Notecraft"]) == "Notecraft"


def test_ordinary_words_are_left_alone():
    # a real sentence that shares no near-miss with any term must pass through untouched, so the
    # corrector never mangles normal speech.
    terms = ["Notecraft", "WaveShaper", "Skylark"]
    assert correct_terms("let's talk about the weather today", terms) == "let's talk about the weather today"
    assert correct_terms("", terms) == ""


def test_nothing_is_corrected_without_a_term_list():
    assert correct_terms("open notcraft", []) == "open notcraft"


def test_common_words_close_to_a_term_are_not_corrupted():
    # from real recordings: below the threshold these near-collisions creep in. The terminator
    # "over" turning into a project called "Overlay" (0.73) would keep a turn from ever ending,
    # and "are" is just as close to "Arena" (0.75) - guard both hard.
    terms = ["Overlay", "Arena", "WaveShaper", "Notecraft", "Skylark"]
    assert correct_terms("are you there over", terms) == "are you there over"
    assert correct_terms("tell me over", terms) == "tell me over"


def test_the_nearest_of_several_terms_wins():
    terms = ["Notecraft", "WaveShaper", "Skylark"]
    assert correct_terms("start waveshapr", terms) == "start WaveShaper"  # closest to WaveShaper


def test_a_multi_word_domain_term_is_corrected_as_a_phrase():
    # Domain vocabulary is often several words ("Bayesian inference", "Gray Scale"), which a
    # token-at-a-time pass can never fix - the phrase has to be matched as a whole.
    terms = ["Bayesian inference", "Gray Scale"]
    assert correct_terms("i use bayesan inference daily", terms) == "i use Bayesian inference daily"
    assert correct_terms("a grey scale image", terms) == "a Gray Scale image"


def test_a_two_word_term_run_together_into_one_token_is_still_corrected():
    # Speech-to-text hears "Git Bash" as one word and writes its own compound for it. Comparing a
    # one-token window only against one-word terms left that unfixable, however close it sounded.
    assert correct_terms("pop open a GitMash tab", ["Git Bash"]) == "pop open a Git Bash tab"
    assert correct_terms("open gitbash", ["Git Bash"]) == "open Git Bash"


def test_a_phrase_is_not_glued_across_a_sentence_boundary():
    # "...the inference. Bayesian is..." must not collapse into one phrase.
    assert correct_terms("about inference. Bayesian is fine", ["inference bayesian"]) == "about inference. Bayesian is fine"


def test_multi_word_terms_do_not_disturb_single_word_matching():
    terms = ["Bayesian inference", "Notecraft"]
    assert correct_terms("open notcraft now", terms) == "open Notecraft now"
    assert correct_terms("nothing to see here", terms) == "nothing to see here"


def test_a_named_translation_is_applied_exactly():
    # Some mishearings are not near misses at all - "Claude agent" comes back as "cloud agent",
    # which is two perfectly ordinary words no similarity score will ever flag. Those are named
    # outright, in a list they can read and add to.
    said = correct_terms("how's our cloud agent doing", [], translations={"cloud agent": "Claude agent"})

    assert said == "how's our Claude agent doing"


def test_a_translation_is_heard_however_it_was_capitalised_and_keeps_the_sentence_punctuation():
    translations = {"cloud agent": "Claude agent"}

    assert correct_terms("Ask the Cloud Agent.", [], translations=translations) == \
        "Ask the Claude agent."
    assert correct_terms("(cloud agent)", [], translations=translations) == "(Claude agent)"


def test_a_named_translation_wins_over_a_near_miss():
    # The whole point of naming one is that it beats the guess, and the guess can be a perfect
    # match: a folder called "cloud" makes "Cloud" a known term, so what they say as "Claude" and
    # Parakeet writes as "cloud" would be corrected confidently to the wrong word.
    said = correct_terms("ask cloud about it", ["Cloud"], translations={"cloud": "Claude"})

    assert said == "ask Claude about it"


def test_the_translations_that_ship_are_the_ones_actually_heard():
    # Counted in their own session transcripts rather than imagined: these are the words this app is
    # about, arriving as ordinary English no similarity score will ever flag.
    assert DEFAULT_TRANSLATIONS["cloud agent"] == "Claude agent"
    assert DEFAULT_TRANSLATIONS["work tree"] == "worktree"
    # Looked up lowercased, so a left-hand side with a capital in it would simply never match.
    assert all(heard == heard.lower() for heard in DEFAULT_TRANSLATIONS)


def test_scan_turns_directory_names_into_spoken_terms(tmp_path):
    for name in ["notecraft", "wave_shaper", "skylark", "mp3_tagger"]:
        (tmp_path / name).mkdir()
    assert scan_terms([tmp_path]) == {"Notecraft", "WaveShaper", "Skylark", "Mp3Tagger"}


def test_scan_keeps_a_names_own_capitalisation(tmp_path):
    (tmp_path / "OpenGLDemo").mkdir()
    (tmp_path / "PDFMerger").mkdir()
    assert scan_terms([tmp_path]) == {"OpenGLDemo", "PDFMerger"}  # not "Opengldemo"


def test_scan_ignores_files_hidden_and_scaffolding_dirs(tmp_path):
    (tmp_path / "skylark").mkdir()
    (tmp_path / "_ARCHIVE").mkdir()  # leading underscore - scaffolding, not a project
    (tmp_path / ".git").mkdir()  # hidden
    (tmp_path / "notes.txt").write_text("x")  # a file, not a project dir
    assert scan_terms([tmp_path]) == {"Skylark"}


def test_scan_drops_short_and_generic_names(tmp_path):
    for name in ["ab", "src", "vision", "kestrel"]:  # too short / infrastructure / common word
        (tmp_path / name).mkdir()
    assert scan_terms([tmp_path]) == {"Kestrel"}  # only the distinctive one survives


def test_scan_survives_a_missing_root(tmp_path):
    assert scan_terms([tmp_path / "does-not-exist"]) == set()
