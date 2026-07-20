"""The window as an application: a browser engine in an OS window of its own, not a browser tab.

Same shape as Notecraft, which this is moving to join - Flask on a loopback port nobody else can
reach, and pywebview holding a WebView2 view of it. What that buys over a tab is what a companion
needs: its own taskbar button and icon, no address bar, no other tabs, and a window that is closed
rather than navigated away from.

The server thread is a daemon: when the window closes the process ends, and a served page nobody
can see must not keep it alive.
"""

import socket
import threading

WINDOW = {"width": 980, "height": 760, "min_size": (620, 520)}


def free_port(host="127.0.0.1"):
    """A port the machine says is free. Fixed ports collide with whatever already holds them,
    and this is loopback-only, so nothing needs to know it in advance."""
    with socket.socket() as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


def serve(app, port, host="127.0.0.1"):
    """Run the app on a thread that cannot outlive the window."""
    thread = threading.Thread(
        target=lambda: app.run(host=host, port=port, debug=False, use_reloader=False,
                               threaded=True),
        daemon=True, name="entity-web")
    thread.start()
    return thread


def open_window(app, *, title="Entity", icon=None, webview=None, port=None):
    """Show the app in its own window, and return when that window is closed.

    `webview` is injected so the wiring can be exercised without a display; a machine without
    pywebview raises, rather than quietly opening a browser tab - a tab is the thing this exists
    not to be."""
    if webview is None:
        import webview  # noqa: PLC0415 - optional at import time, required to show a window

    port = port or free_port()
    serve(app, port)
    webview.create_window(title, f"http://127.0.0.1:{port}/", **WINDOW)
    webview.start(icon=icon) if icon else webview.start()
