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

from entity.mirror import APP_ID

WINDOW = {"width": 980, "height": 760, "min_size": (620, 520)}


def _set_app_id_via_shell32(app_id):
    import ctypes

    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)


def set_app_id(app_id, api=_set_app_id_via_shell32):
    """Claim a taskbar identity. Must happen before the window exists, and a platform without the
    API just doesn't get one - a cosmetic nicety must never keep the window from opening."""
    try:
        api(app_id)
    except Exception:
        pass


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

    # Before the window exists, or the taskbar button is already grouped. Windows groups buttons by
    # AppUserModelID, and a process that declares none inherits the identity of whatever other
    # pythonw-hosted app already owns one - the Entity window turned up under another app's icon,
    # wearing its name. It did so again the moment this call was dropped in a move.
    set_app_id(APP_ID)
    port = port or free_port()
    serve(app, port)
    webview.create_window(title, f"http://127.0.0.1:{port}/", **WINDOW)
    # pywebview's winforms backend applies the icon to the window, and so to the taskbar button;
    # without it Windows shows pythonw.exe's. (Its "GTK/QT only" docstring is stale.)
    webview.start(icon=icon) if icon else webview.start()
