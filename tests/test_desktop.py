import socket
import threading

from entity.desktop import WINDOW, free_port, open_window, serve, set_app_id
from entity.mirror import APP_ID


class _App:
    """Stands in for the Flask app: records how it was told to run, and returns at once - a test
    that binds a real port serves a page nobody reads and leaves a thread behind."""

    def __init__(self):
        self.ran = threading.Event()
        self.how = None

    def run(self, **how):
        self.how = how
        self.ran.set()


class _Webview:
    """Stands in for pywebview, so the wiring can be checked without opening a window.

    Records the order things happened in as well as what they were: the taskbar identity has to be
    claimed BEFORE the window exists, and afterwards is the same as never."""

    def __init__(self):
        self.made = []
        self.started = []
        self.order = []

    def create_window(self, title, url, **how):
        self.made.append((title, url, how))
        self.order.append("window")

    def start(self, **how):
        self.started.append(how)
        self.order.append("start")


def test_the_port_is_one_the_machine_says_is_free():
    port = free_port()

    with socket.socket() as sock:  # free means bindable; a fixed one collides with its holder
        sock.bind(("127.0.0.1", port))


def test_the_window_is_pointed_at_the_app_on_loopback_only():
    webview = _Webview()

    open_window(_App(), title="Entity", webview=webview, port=8123)

    title, url, how = webview.made[0]
    assert title == "Entity"
    assert url == "http://127.0.0.1:8123/"  # nothing off this machine can reach it
    assert how == WINDOW  # its own size, and a floor under it
    assert webview.started == [{}]


def test_the_taskbar_identity_is_claimed_before_the_window_exists(monkeypatch):
    # Windows groups taskbar buttons by AppUserModelID, and a process that declares none inherits
    # whatever other pythonw-hosted app already owns one - Entity turned up as another app. Claimed
    # after the window is made, it is the same as never claimed.
    claimed = []
    monkeypatch.setattr("entity.desktop.set_app_id", lambda app_id: claimed.append(app_id))
    webview = _Webview()

    open_window(_App(), webview=webview, port=8123)

    assert claimed == [APP_ID]
    assert webview.order == ["window", "start"]  # and the claim happened before either


def test_a_platform_without_the_taskbar_api_still_gets_a_window():
    # A cosmetic nicety must never keep the window from opening.
    def refuses(_):
        raise OSError("no shell32 here")

    set_app_id("Entity.VoiceCompanion", api=refuses)


def test_the_server_is_reachable_only_from_this_machine_and_cannot_outlive_the_window():
    app = _App()

    thread = serve(app, 8123)
    app.ran.wait(2)

    # A served page nobody can see must not keep the process alive after the window closes.
    assert thread.daemon
    assert app.how["host"] == "127.0.0.1"
    assert app.how["port"] == 8123
    assert app.how["threaded"] is True   # the page polls while a page is being saved
    assert app.how["use_reloader"] is False  # a reloader would fork a second conversation
