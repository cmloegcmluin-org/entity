from entity.steps import DID, HEAD_LINES, INDENT, LINE_CHARS, SAID, TAIL_LINES, render


class FakeText:
    def __init__(self, text):
        self.text = text


class FakeCall:
    def __init__(self, name, tool_input):
        self.name = name
        self.input = tool_input


class FakeResult:
    def __init__(self, content, is_error=None):
        self.tool_use_id = "toolu_01"
        self.content = content
        self.is_error = is_error


class FakeMsg:
    def __init__(self, content):
        self.content = content


def test_a_tool_call_is_logged_as_the_command_it_ran():
    # The log used to hold only what the agent NARRATED, so the run itself - the command, the test
    # output, the diff - was never written down at all.
    message = FakeMsg([FakeCall("Bash", {"command": "python -m pytest -q"})])

    assert render(message) == [(DID, "Bash: python -m pytest -q")]


def test_a_call_is_named_by_whatever_that_tool_acts_on():
    # Every tool says what it touched in its own key; a Read that logged an empty target would be
    # the same silence the narrated-only log already was.
    calls = [
        FakeCall("Read", {"file_path": "src/entity/gui.py"}),
        FakeCall("Grep", {"pattern": "on_step", "path": "src"}),
        FakeCall("Glob", {"pattern": "**/*.py"}),
    ]

    assert render(FakeMsg(calls)) == [
        (DID, "Read: src/entity/gui.py"),
        (DID, "Grep: on_step"),
        (DID, "Glob: **/*.py"),
    ]


def test_an_edit_shows_the_diff_it_made():
    # "no tool calls, diffs, or command/test output" - an edit whose lines are missing says only
    # that a file was touched, which is the part nobody needed telling.
    call = FakeCall("Edit", {
        "file_path": "src/entity/sdk_session.py",
        "old_string": "latest = \"\"\nfor message in messages:",
        "new_string": "lines = []\nfor message in messages:",
    })

    assert render(FakeMsg([call])) == [
        (DID, "Edit: src/entity/sdk_session.py"),
        (DID, "    - latest = \"\""),
        (DID, "    - for message in messages:"),
        (DID, "    + lines = []"),
        (DID, "    + for message in messages:"),
    ]


def test_a_write_shows_the_file_it_laid_down():
    # A whole new file is a diff too - all of it added.
    call = FakeCall("Write", {"file_path": "src/entity/steps.py", "content": "SAID = 1\nDID = 2"})

    assert render(FakeMsg([call])) == [
        (DID, "Write: src/entity/steps.py"),
        (DID, "    + SAID = 1"),
        (DID, "    + DID = 2"),
    ]


def test_what_a_tool_gave_back_is_logged_under_its_call():
    # The test output itself - the thing that says whether the work is actually green.
    message = FakeMsg([FakeResult("collected 358 items\n\n358 passed in 4.41s")])

    assert render(message) == [
        (DID, "    collected 358 items"),
        (DID, ""),  # a blank line stays blank rather than becoming indent nobody can see
        (DID, "    358 passed in 4.41s"),
    ]


def test_a_result_delivered_in_blocks_reads_the_same_as_one_delivered_as_text():
    # The SDK hands a result back either way; a log that only understood one of them would drop
    # whole commands on the floor depending on which shape came back.
    message = FakeMsg([FakeResult([{"type": "text", "text": "one"}, {"type": "text", "text": "two"}])])

    assert render(message) == [(DID, "    one"), (DID, "    two")]


def test_a_result_with_nothing_in_it_writes_nothing():
    assert render(FakeMsg([FakeResult(None)])) == []


def test_a_huge_result_is_cut_from_the_middle_and_says_how_much_it_dropped():
    # The tab has to stay something a person reads. Reading a file back whole would bury the
    # session in source - but a silent cut would be the same missing evidence in a new costume,
    # so the count of what went is written down where it went.
    body = "\n".join(f"line {number}" for number in range(1, 201))

    lines = [text for _, text in render(FakeMsg([FakeResult(body)]))]

    assert lines[0] == "    line 1"
    assert lines[-1] == "    line 200"  # the tail is where a run says how it came out
    assert len(lines) == HEAD_LINES + 1 + TAIL_LINES
    assert lines[HEAD_LINES] == f"    … {200 - HEAD_LINES - TAIL_LINES} more lines …"


def test_what_the_agent_said_and_what_it_did_stay_apart_and_in_order():
    # The tab draws them differently - its words as messages, its work as the machinery under
    # them - so the two are told apart here rather than in the window.
    message = FakeMsg([
        FakeText("Confirmed red. Now the implementation:"),
        FakeCall("Bash", {"command": "git status --short"}),
    ])

    assert render(message) == [
        (SAID, "Confirmed red. Now the implementation:"),
        (DID, "Bash: git status --short"),
    ]


def test_a_written_file_is_capped_like_any_other_output():
    # A whole file laid down in one call is as big as any command's output, and buries the tab
    # just the same.
    call = FakeCall("Write", {"file_path": "big.py", "content": "\n".join("x" for _ in range(200))})

    lines = [text for _, text in render(FakeMsg([call]))]

    assert lines[0] == "Write: big.py"
    assert len(lines) == 1 + HEAD_LINES + 1 + TAIL_LINES
    assert f"… {200 - HEAD_LINES - TAIL_LINES} more lines …" in lines[1 + HEAD_LINES]


def test_one_enormous_line_is_cut_where_it_stops_being_readable():
    # A minified blob or a base64 payload arrives as ONE line, so a line-count cap never touches
    # it - and the tab would carry a single message tens of thousands of characters wide.
    lines = [text for _, text in render(FakeMsg([FakeResult("x" * 5000)]))]

    assert lines == [(INDENT + "x" * 5000)[:LINE_CHARS] + "…"]


def test_the_agents_own_words_are_never_cut_short():
    # The width cap is for the machinery. A summary is the part being read, and cutting it
    # mid-sentence would be the same missing evidence this module exists to end.
    said = "I picked up the branch, oriented, and finished the feature with TDD. " * 40

    assert render(FakeMsg([FakeText(said)])) == [(SAID, said.strip())]


def test_a_command_and_a_diff_line_are_cut_the_same_way_output_is():
    blocks = [
        FakeCall("Bash", {"command": "y" * 5000}),
        FakeCall("Edit", {"file_path": "a.py", "old_string": "z" * 5000, "new_string": ""}),
    ]

    lines = [text for _, text in render(FakeMsg(blocks))]

    assert lines == [
        ("Bash: " + "y" * 5000)[:LINE_CHARS] + "…",
        "Edit: a.py",
        (INDENT + "- " + "z" * 5000)[:LINE_CHARS] + "…",
    ]


def test_a_call_that_failed_is_marked_as_failed():
    # Whether a command actually worked is the first thing anyone reads a log for; an error that
    # looks exactly like output answers that question wrong.
    message = FakeMsg([FakeResult("fatal: not a git repository\nuse --help", is_error=True)])

    assert render(message) == [
        (DID, "    ! fatal: not a git repository"),
        (DID, "    ! use --help"),
    ]


def test_a_message_carrying_plain_text_is_not_read_letter_by_letter():
    # A user message arrives with its content as ONE string - the prompt the desk itself just sent
    # and already logged. Iterating it treats every character as a block.
    assert render(FakeMsg("fix the drive link")) == []


def test_a_call_with_nothing_to_name_is_still_logged_as_having_happened():
    # Not every tool acts on a path or a command. A dangling "TodoWrite: " reads as a tool whose
    # target went missing rather than one that never had one.
    assert render(FakeMsg([FakeCall("TodoWrite", {"todos": []})])) == [(DID, "TodoWrite")]
