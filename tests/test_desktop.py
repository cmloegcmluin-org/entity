import socket
import threading

from entity.desktop import WINDOW, free_port, open_window, serve


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
    """Stands in for pywebview, so the wiring can be checked without opening a window."""

    def __init__(self):
        self.made = []
        self.started = []

    def create_window(self, title, url, **how):
        self.made.append((title, url, how))

    def start(self, **how):
        self.started.append(how)


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
