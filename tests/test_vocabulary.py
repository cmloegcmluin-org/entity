from entity.vocabulary import correct_terms, scan_terms


def test_a_near_miss_token_is_corrected_to_the_known_term():
    # Parakeet misheard his coined word; the closest known term above the bar wins.
    assert correct_terms("open high ideas", ["Notecraft"]) == "open high ideas"
    assert correct_terms("open hideas", ["Notecraft"]) == "open Notecraft"


def test_surrounding_punctuation_is_preserved_and_matched_on_the_bare_word():
    # the period must not drag the similarity down, and it must survive the swap.
    assert correct_terms("go to hideas.", ["Notecraft"]) == "go to Notecraft."
    assert correct_terms("(waveshaper)", ["WaveShaper"]) == "(WaveShaper)"


def test_an_exact_word_is_recased_to_the_canonical_spelling():
    # Parakeet got the sounds right but not his capitalisation; hand back his spelling.
    assert correct_terms("open notecraft now", ["Notecraft"]) == "open Notecraft now"
    assert correct_terms("Notecraft", ["Notecraft"]) == "Notecraft"


def test_ordinary_words_are_left_alone():
    # a real sentence that shares no near-miss with any term must pass through untouched, so the
    # corrector never mangles normal speech.
    terms = ["Notecraft", "WaveShaper", "Skylark"]
    assert correct_terms("let's talk about the weather today", terms) == "let's talk about the weather today"
    assert correct_terms("", terms) == ""


def test_nothing_is_corrected_without_a_term_list():
    assert correct_terms("open hideas", []) == "open hideas"


def test_common_words_close_to_a_term_are_not_corrupted():
    # from his real recordings: below the threshold these near-collisions creep in. The terminator
    # "over" turning into "Evolver" would keep his turns from ever ending - guard it hard.
    terms = ["Evolver", "Harem", "WaveShaper", "Notecraft", "Skylark"]
    assert correct_terms("are you there over", terms) == "are you there over"
    assert correct_terms("tell me over", terms) == "tell me over"


def test_the_nearest_of_several_terms_wins():
    terms = ["Notecraft", "WaveShaper", "Skylark"]
    assert correct_terms("start funtim", terms) == "start WaveShaper"  # closest to WaveShaper, not the others


def test_a_multi_word_domain_term_is_corrected_as_a_phrase():
    # Domain vocabulary is often several words ("Bayesian notation", "Gray Area"), which a
    # token-at-a-time pass can never fix - the phrase has to be matched as a whole.
    terms = ["Bayesian notation", "Gray Area"]
    assert correct_terms("i use sagital notation daily", terms) == "i use Bayesian notation daily"
    assert correct_terms("his grey area residency", terms) == "his Gray Area residency"


def test_a_phrase_is_not_glued_across_a_sentence_boundary():
    # "...the notation. Bayesian is..." must not collapse into one phrase.
    assert correct_terms("about notation. Bayesian is his", ["notation bayesian"]) == "about notation. Bayesian is his"


def test_multi_word_terms_do_not_disturb_single_word_matching():
    terms = ["Bayesian notation", "Notecraft"]
    assert correct_terms("open hideas now", terms) == "open Notecraft now"
    assert correct_terms("nothing to see here", terms) == "nothing to see here"


def test_scan_turns_his_directory_names_into_spoken_terms(tmp_path):
    for name in ["notecraft", "wave_shaper", "skylark", "osr2_broker"]:
        (tmp_path / name).mkdir()
    assert scan_terms([tmp_path]) == {"Notecraft", "WaveShaper", "Skylark", "Osr2Broker"}


def test_scan_keeps_a_names_own_capitalisation(tmp_path):
    (tmp_path / "ComfyUIApp").mkdir()
    (tmp_path / "FunGenApp").mkdir()
    assert scan_terms([tmp_path]) == {"ComfyUIApp", "FunGenApp"}  # not "Comfyuiapp"


def test_scan_ignores_files_hidden_and_scaffolding_dirs(tmp_path):
    (tmp_path / "skylark").mkdir()
    (tmp_path / "_ARCHIVE").mkdir()  # leading underscore - scaffolding, not a project
    (tmp_path / ".git").mkdir()  # hidden
    (tmp_path / "notes.txt").write_text("x")  # a file, not a project dir
    assert scan_terms([tmp_path]) == {"Skylark"}


def test_scan_drops_short_and_generic_names(tmp_path):
    for name in ["ab", "src", "vision", "harem"]:  # too short / infrastructure / common word ; keep harem
        (tmp_path / name).mkdir()
    assert scan_terms([tmp_path]) == {"Harem"}


def test_scan_survives_a_missing_root(tmp_path):
    assert scan_terms([tmp_path / "does-not-exist"]) == set()
