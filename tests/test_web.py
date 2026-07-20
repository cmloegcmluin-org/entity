from entity.gui import TranscriptModel
from entity.web import create_app


def _model(*lines):
    model = TranscriptModel(clock=lambda: "12:00:00")
    for line in lines:
        model.apply("history", line)
    return model


def test_the_page_hands_the_conversation_to_the_window_it_draws():
    model = _model("===== 2026-07-18 =====",
                   "[02:41:38] you said: morning",
                   "[02:42:10] entity> Morning.")
    client = create_app(model, on_submit=lambda text: None).test_client()

    shown = client.get("/messages").get_json()

    assert [entry["role"] for entry in shown["entries"]] == ["day", "you", "entity"]
    assert shown["entries"][1]["name"] == "You"  # who said it, resolved once, on the server
    assert shown["entries"][2]["name"] == "Entity"
    assert shown["sessions"] == [{"label": "2026-07-18 02:41", "at": 0}]


def test_a_poll_carries_only_what_the_page_has_not_drawn():
    # Four times a second against every session ever recorded, so it cannot hand back the lot.
    model = _model("===== 2026-07-18 =====",
                   "[02:41:38] you said: morning",
                   "[02:42:10] entity> Morning.")
    client = create_app(model, on_submit=lambda text: None).test_client()

    shown = client.get("/messages?since=2").get_json()

    assert [entry["text"] for entry in shown["entries"]] == ["Morning."]
    assert (shown["at"], shown["total"]) == (2, 3)  # where it starts, and how much there now is
    assert client.get("/messages?since=99").get_json()["entries"] == []  # never past the end
