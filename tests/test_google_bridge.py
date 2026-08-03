"""The stdio bridge to Google's hosted MCP servers.

It exists because the CLI's own remote transport fails against them: the tools/list response
comes back VALID and the transport throws anyway - "Failed to fetch tools: ... Error POSTing to
endpoint: {...\"result\":{\"tools\":[..." with the successful body inside the error, on 2.1.212
and 2.1.220 both, read straight out of the CLI's own MCP logs. Stdio servers are the transport
that demonstrably works, so the bridge speaks stdio to the CLI and plain HTTPS to Google.
"""

import json

from excephalon.google_bridge import Bridge


class FakePost:
    """The HTTPS side, scripted: each call pops the next (status, content_type, body) answer."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []  # (url, payload_dict, auth_header)

    def __call__(self, url, body, headers):
        try:
            payload = json.loads(body)
        except ValueError:
            payload = body.decode("utf-8")  # the refresh trade is form-encoded, not JSON
        self.calls.append((url, payload, headers.get("Authorization")))
        status, content_type, answer = self.answers.pop(0)
        return status, content_type, answer


class Tokens:
    """A token store held in memory: what refresh writes is what later reads see."""

    def __init__(self, access=None, refresh=None):
        self.held = {k: v for k, v in (("access_token", access), ("refresh_token", refresh)) if v}

    def read(self):
        return dict(self.held)

    def write(self, tokens):
        self.held = dict(tokens)


URL = "https://gmailmcp.googleapis.com/mcp/v1"


def _initialize_line(request_id=1):
    return json.dumps({"jsonrpc": "2.0", "id": request_id, "method": "initialize",
                       "params": {"protocolVersion": "2025-06-18"}})


def test_a_request_line_is_forwarded_and_its_answer_comes_back_as_a_line():
    result = {"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {"name": "StatelessServer"}}}
    post = FakePost([(200, "application/json; charset=UTF-8", json.dumps(result))])
    bridge = Bridge(URL, post=post, tokens=Tokens(access="tok-1"))

    answer = bridge.handle(_initialize_line())

    assert json.loads(answer) == result
    [(url, payload, auth)] = post.calls
    assert url == URL and payload["method"] == "initialize"
    assert auth == "Bearer tok-1"


def test_an_expired_token_is_refreshed_once_and_the_request_retried():
    # Google access tokens die hourly. The bridge notices the 401, trades the refresh token for a
    # fresh access token, writes it down for every later request, and retries - all invisible to
    # the CLI, which just sees its answer.
    result = {"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}
    post = FakePost([
        (401, "application/json", ""),
        (200, "application/json", json.dumps({"access_token": "tok-new", "expires_in": 3599})),
        (200, "application/json", json.dumps(result)),
    ])
    tokens = Tokens(access="tok-stale", refresh="refresh-1")
    bridge = Bridge(URL, post=post, tokens=tokens,
                    client={"client_id": "id-1", "client_secret": "secret-1"})

    answer = bridge.handle(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))

    assert json.loads(answer) == result
    refresh_call = post.calls[1]
    assert "oauth2.googleapis.com/token" in refresh_call[0]
    assert post.calls[2][2] == "Bearer tok-new"  # the retry wears the fresh token
    assert tokens.held["access_token"] == "tok-new"  # and it is written down, not just used once
    assert tokens.held["refresh_token"] == "refresh-1"  # the refresh token itself survives


def test_unauthorized_with_no_way_back_answers_with_a_plain_error_naming_the_fix():
    # No tokens at all, or a refresh that fails: the CLI must get a well-formed JSON-RPC error -
    # a bridge that crashes or goes silent is a server that "failed", with nothing saying why.
    # The message names the one thing the user can do.
    post = FakePost([(401, "application/json", "")])
    bridge = Bridge(URL, post=post, tokens=Tokens(), client={})

    answer = json.loads(bridge.handle(json.dumps(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})))

    assert answer["id"] == 3
    assert "Connect Google" in answer["error"]["message"]


def test_a_notification_is_forwarded_but_owes_no_answer_line():
    # notifications/initialized has no id; JSON-RPC forbids answering it, and a line written
    # anyway would desync the whole stdio stream.
    post = FakePost([(202, "text/plain", "")])
    bridge = Bridge(URL, post=post, tokens=Tokens(access="tok-1"))

    answer = bridge.handle(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    assert answer is None
    assert post.calls  # forwarded all the same


def test_an_sse_wrapped_answer_is_unwrapped_to_its_json():
    # The spec lets the server answer a POST as a one-event SSE stream instead of plain JSON;
    # the CLI's stdio side speaks only JSON lines, so the unwrap happens here.
    result = {"jsonrpc": "2.0", "id": 4, "result": {"ok": True}}
    sse = f"event: message\ndata: {json.dumps(result)}\n\n"
    post = FakePost([(200, "text/event-stream", sse)])
    bridge = Bridge(URL, post=post, tokens=Tokens(access="tok-1"))

    assert json.loads(bridge.handle(json.dumps(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/list"}))) == result


def test_the_client_file_google_hands_out_is_read_as_it_comes(tmp_path):
    # The console's download wraps the values in {"installed": {...}} (or {"web": ...}); asking
    # him to rewrap them is asking a person to be a JSON parser. Drop the file in as it came.
    from excephalon.google_bridge import load_client

    downloaded = tmp_path / "client.json"
    downloaded.write_text(json.dumps({"installed": {
        "client_id": "id-9", "client_secret": "secret-9",
        "token_uri": "https://oauth2.googleapis.com/token"}}), encoding="utf-8")
    assert load_client(downloaded)["client_id"] == "id-9"

    flat = tmp_path / "flat.json"
    flat.write_text(json.dumps({"client_id": "id-3"}), encoding="utf-8")
    assert load_client(flat)["client_id"] == "id-3"

    assert load_client(tmp_path / "absent.json") == {}


def test_tokens_survive_the_trip_to_disk(tmp_path):
    from excephalon.google_bridge import FileTokens

    store = FileTokens(tmp_path / "google" / "tokens.json")
    assert store.read() == {}  # never signed in: empty, not an error

    store.write({"access_token": "a", "refresh_token": "r"})
    again = FileTokens(tmp_path / "google" / "tokens.json")
    assert again.read() == {"access_token": "a", "refresh_token": "r"}


def test_the_sign_in_url_asks_for_a_refresh_token_and_only_the_needed_scopes():
    # access_type=offline + prompt=consent is what makes Google hand over a REFRESH token - the
    # first sign-in without them yields an access token that dies in an hour with no way back,
    # and the bridge would ask him to sign in again every hour of his life.
    from excephalon.google_bridge import SCOPES, auth_url

    url = auth_url({"client_id": "id-7"}, port=49152)

    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "access_type=offline" in url and "prompt=consent" in url
    assert "client_id=id-7" in url
    assert "127.0.0.1%3A49152" in url  # the loopback catcher the browser is sent back to
    for scope in SCOPES:
        from urllib.parse import quote
        assert quote(scope, safe="") in url


def test_the_code_google_sends_back_is_traded_for_tokens_and_written_down(tmp_path):
    from excephalon.google_bridge import FileTokens, exchange_code

    post = FakePost([(200, "application/json", json.dumps(
        {"access_token": "tok-1", "refresh_token": "refresh-1", "expires_in": 3599}))])
    store = FileTokens(tmp_path / "tokens.json")

    exchange_code("the-code", {"client_id": "id-7", "client_secret": "s-7"},
                  port=49152, tokens=store, post=post)

    assert store.read()["refresh_token"] == "refresh-1"
    [(url, payload, _)] = post.calls
    assert url == "https://oauth2.googleapis.com/token"
    assert "code=the-code" in payload and "grant_type=authorization_code" in payload


def test_initialize_is_forwarded_at_the_protocol_version_google_accepts():
    # Google 401s any initialize below protocolVersion 2025-06-18 (measured: 2024-11-05 and
    # 2025-03-26 both bounce) instead of negotiating downward as the spec intends - and a 401
    # here read as "not signed in", which sent the user to a sign-in that could not help. The
    # bridge speaks the accepted version to Google whatever the CLI opened with; the response
    # carries it back, and the CLI adapts, which is the negotiation working one hop early.
    result = {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-06-18"}}
    post = FakePost([(200, "application/json", json.dumps(result))])
    bridge = Bridge(URL, post=post, tokens=Tokens(access="tok-1"))

    old = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": "2024-11-05", "capabilities": {}}})
    assert json.loads(bridge.handle(old)) == result

    [(_, payload, _)] = post.calls
    assert payload["params"]["protocolVersion"] == "2025-06-18"
    assert payload["params"]["capabilities"] == {}  # the rest of the request rides untouched
