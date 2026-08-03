"""A stdio MCP bridge to Google's hosted Gmail and Calendar servers.

Google runs real MCP servers (gmailmcp.googleapis.com, calendarmcp.googleapis.com) and the CLI's
remote transport cannot talk to them: the tools/list response comes back VALID and the transport
throws anyway - its own log shows "Failed to fetch tools: ... Error POSTing to endpoint:" with
the successful tool list inside the error, on 2.1.212 and 2.1.220 both. Stdio servers are the
transport that demonstrably works, so this speaks newline-delimited JSON-RPC to the CLI on stdin
and stdout, and plain HTTPS to Google, forwarding each line as it is.

It also owns its auth, which fixes the other half of the story: Google's sign-in refuses to
register clients on the fly (no dynamic client registration), and the CLI keeps what tokens it
does win in the macOS Keychain, where a headless session may not be able to follow. Here the
user's own OAuth client (runtime/google/client.json, the file Google's console hands out) and
the tokens (runtime/google/tokens.json) live in runtime/ - personal, gitignored, and readable
by every session the app spawns, on either desk. `--connect` runs the one-time browser sign-in;
`--serve <url>` is what the CLI launches per server.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

# Where the personal half lives, found from this file so the bridge works whatever cwd the CLI
# spawns it with - an errand session's, his interactive shell's, anyone's.
RUNTIME_GOOGLE = Path(__file__).resolve().parents[2] / "runtime" / "google"

TOKEN_URL = "https://oauth2.googleapis.com/token"

# What the user is asked to grant, chosen from each server's own advertised scopes: enough to
# read, organize, draft and send mail and to keep his calendar - none of the permanent-delete or
# settings scopes, which nothing here needs.
SCOPES = (
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar",
)

CONNECT_HINT = ("Google is not connected yet - run Connect Google.command (or "
                "`python -m excephalon.google_bridge --connect`) and sign in once.")

# The one protocol version Google's servers accept. They 401 any initialize below it (measured:
# 2024-11-05 and 2025-03-26 both bounce) instead of negotiating downward as the spec intends -
# and that 401 read as "not signed in", which sent the user to a sign-in that could not help.
GOOGLE_PROTOCOL = "2025-06-18"


def load_client(path):
    """His OAuth client, from the file Google's console hands out, dropped in as it came.

    The download wraps the values in {"installed": {...}} (or {"web": ...}); asking him to
    rewrap them is asking a person to be a JSON parser. A flat file works too, and an absent or
    unreadable one is {} - the bridge still serves, and the CONNECT_HINT is what says why
    nothing is signed in."""
    try:
        held = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(held, dict):
        return {}
    return held.get("installed") or held.get("web") or held


class FileTokens:
    """The tokens, in runtime/google/tokens.json: personal, gitignored, and readable by every
    session the app spawns - which the Keychain, where the CLI keeps its own, may not be."""

    def __init__(self, path):
        self._path = Path(path)

    def read(self):
        try:
            held = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return held if isinstance(held, dict) else {}

    def write(self, tokens):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(tokens), encoding="utf-8")
        os.chmod(self._path, 0o600)  # his sign-in; no other account on the machine needs it


def _unwrap(content_type, body):
    """The JSON a response carries, whether it came as plain JSON or a one-event SSE stream."""
    if "text/event-stream" not in (content_type or ""):
        return body
    held = [line[5:].strip() for line in body.splitlines() if line.startswith("data:")]
    return held[-1] if held else ""


AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


def auth_url(client, *, port):
    """Where the browser goes to sign in. access_type=offline + prompt=consent is what makes
    Google hand over a REFRESH token - without them the first sign-in yields an access token
    that dies in an hour with no way back, and he would be asked to sign in every hour."""
    return AUTH_URL + "?" + urlencode({
        "client_id": client.get("client_id", ""),
        "redirect_uri": f"http://127.0.0.1:{port}",
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    })


def exchange_code(code, client, *, port, tokens, post):
    """Trade the one-time code Google sent back for the tokens, and write them down."""
    status, _, body = post(
        TOKEN_URL,
        urlencode({"code": code,
                   "client_id": client.get("client_id", ""),
                   "client_secret": client.get("client_secret", ""),
                   "redirect_uri": f"http://127.0.0.1:{port}",
                   "grant_type": "authorization_code"}).encode("utf-8"),
        {"Content-Type": "application/x-www-form-urlencoded"})
    if status != 200:
        raise RuntimeError(f"Google declined the sign-in code: {body[:200]}")
    fresh = json.loads(body)
    tokens.write({"access_token": fresh.get("access_token", ""),
                  "refresh_token": fresh.get("refresh_token", "")})


class Bridge:
    """One server's forwarding: a JSON-RPC line in, the same request against Google, a line back."""

    def __init__(self, url, *, post, tokens, client=None):
        self._url = url
        self._post = post
        self._tokens = tokens
        self._client = client or {}

    def handle(self, line):
        """Answer one stdin line: the forwarded response as a line, or None when none is owed.

        A 401 is an hourly-expired access token before it is anything else, so the refresh is
        traded and the request retried once, invisibly. Past that, a request with an id gets a
        well-formed JSON-RPC error naming the one thing the user can do - a bridge that crashes
        or goes silent is a server that "failed", with nothing saying why."""
        request = json.loads(line)
        if request.get("method") == "initialize":
            # Speak the version Google accepts whatever the CLI opened with; the response
            # carries it back, and the CLI adapts - the negotiation working one hop early. And
            # introduce the client on THIS wire truthfully: it is the bridge, not the CLI behind
            # it - which also matters mechanically, because Google 401s an initialize whose
            # clientInfo.name is "claude-code" (bisected to that one field; everything else in
            # the request passes).
            params = dict(request.get("params") or {})
            params["protocolVersion"] = GOOGLE_PROTOCOL
            params["clientInfo"] = {"name": "excephalon-google-bridge", "version": "1"}
            line = json.dumps({**request, "params": params})
        status, content_type, body = self._ask(line)
        if status in (401, 403) and self._refresh():
            status, content_type, body = self._ask(line)
        request_id = request.get("id")
        if request_id is None:
            return None  # a notification: answering it would desync the whole stdio stream
        if status in (401, 403):
            return json.dumps({"jsonrpc": "2.0", "id": request_id,
                               "error": {"code": -32000, "message": CONNECT_HINT}})
        return _unwrap(content_type, body)

    def _ask(self, line):
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        access = self._tokens.read().get("access_token")
        if access:
            headers["Authorization"] = f"Bearer {access}"
        return self._post(self._url, line.encode("utf-8"), headers)

    def _refresh(self):
        """Trade the refresh token for a fresh access token, and write it down. False when there
        is nothing to trade or Google declines - the caller falls through to the plain error."""
        held = self._tokens.read()
        if not (held.get("refresh_token") and self._client.get("client_id")):
            return False
        status, _, body = self._post(
            TOKEN_URL,
            urlencode({"grant_type": "refresh_token",
                       "refresh_token": held["refresh_token"],
                       "client_id": self._client["client_id"],
                       "client_secret": self._client.get("client_secret", "")}).encode("utf-8"),
            {"Content-Type": "application/x-www-form-urlencoded"})
        if status != 200:
            return False
        fresh = json.loads(body)
        self._tokens.write({**held, "access_token": fresh["access_token"]})
        return True


def _https_post(url, body, headers):
    """The real HTTPS side: (status, content_type, text). An HTTP error status is an answer here,
    not an exception - the caller's whole job is deciding what a 401 means."""
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return (response.status, response.headers.get("Content-Type", ""),
                    response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as denied:
        return (denied.code, denied.headers.get("Content-Type", ""),
                denied.read().decode("utf-8", errors="replace"))


def serve(url, *, stdin=sys.stdin, stdout=sys.stdout):
    """The stdio loop the CLI runs: one JSON-RPC line in, one out, until stdin closes."""
    bridge = Bridge(url, post=_https_post, tokens=FileTokens(RUNTIME_GOOGLE / "tokens.json"),
                    client=load_client(RUNTIME_GOOGLE / "client.json"))
    for line in stdin:
        if not line.strip():
            continue
        try:
            answer = bridge.handle(line)
        except Exception as exc:  # one bad request must not kill the server for the session
            try:
                request_id = request.get("id")
            except ValueError:
                continue
            if request_id is None:
                continue
            answer = json.dumps({"jsonrpc": "2.0", "id": request_id,
                                 "error": {"code": -32000, "message": f"bridge error: {exc}"}})
        if answer:
            stdout.write(answer.strip() + "\n")
            stdout.flush()


def connect():
    """The one-time browser sign-in: catch Google's redirect on a loopback port, trade the code,
    write the tokens. Run by a person, so it talks in plain sentences."""
    import socket
    import threading
    import webbrowser
    from http.server import BaseHTTPRequestHandler, HTTPServer

    client = load_client(RUNTIME_GOOGLE / "client.json")
    if not client.get("client_id"):
        print(f"No OAuth client found. Put the JSON file Google's console gave you at\n"
              f"  {RUNTIME_GOOGLE / 'client.json'}\nand run this again.")
        return 1

    with socket.socket() as probe:  # a port the machine says is free, for the redirect catcher
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    caught = {}
    done = threading.Event()

    class Catcher(BaseHTTPRequestHandler):
        def do_GET(self):
            caught.update({k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>Signed in - you can close this tab and go back to Excephalon.</h2>")
            done.set()

        def log_message(self, *args):
            pass  # the terminal is for the sentences below, not request logs

    server = HTTPServer(("127.0.0.1", port), Catcher)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    where = auth_url(client, port=port)
    print("Opening your browser for the Google sign-in...")
    webbrowser.open(where)
    print("(if nothing opened, paste this into your browser:)\n  " + where)
    if not done.wait(300):
        print("No sign-in arrived within five minutes - run this again when ready.")
        return 1
    server.shutdown()
    if "code" not in caught:
        print(f"Google sent back an error instead of a sign-in: {caught.get('error', 'unknown')}")
        return 1
    exchange_code(caught["code"], client, port=port,
                  tokens=FileTokens(RUNTIME_GOOGLE / "tokens.json"), post=_https_post)
    print("Connected. Gmail and Google Calendar are signed in; nothing more to do here.")
    return 0


if __name__ == "__main__":
    if "--connect" in sys.argv:
        raise SystemExit(connect())
    if "--serve" in sys.argv:
        serve(sys.argv[sys.argv.index("--serve") + 1])
    else:
        raise SystemExit("usage: python -m excephalon.google_bridge --connect | --serve <url>")
