"""The submit chord: the modifier beside the spacebar, held, then Enter."""

import threading
from types import SimpleNamespace

import pytest

from excephalon.chord import (
    ENTER,
    LWIN,
    WM_KEYDOWN,
    WM_KEYUP,
    WM_SYSKEYDOWN,
    WM_SYSKEYUP,
    ChordListener,
    SubmitChord,
)


def test_windows_key_then_enter_submits():
    submitted = []
    chord = SubmitChord(submit=lambda: submitted.append(True))

    chord.key(LWIN, released=False)
    chord.key(ENTER, released=False)

    assert submitted == [True]


def test_bare_enter_is_left_alone_so_it_still_types_a_newline():
    submitted = []
    chord = SubmitChord(submit=lambda: submitted.append(True))

    chord.key(ENTER, released=False)

    assert submitted == []


def test_held_enter_submits_once_however_long_it_auto_repeats():
    submitted = []
    chord = SubmitChord(submit=lambda: submitted.append(True))

    chord.key(LWIN, released=False)
    for _ in range(5):  # Windows repeats the key-down; there is no key-up between them
        chord.key(ENTER, released=False)

    assert submitted == [True]


def test_a_second_press_submits_again():
    submitted = []
    chord = SubmitChord(submit=lambda: submitted.append(True))

    chord.key(LWIN, released=False)
    chord.key(ENTER, released=False)
    chord.key(ENTER, released=True)
    chord.key(ENTER, released=False)

    assert submitted == [True, True]


def test_letting_go_of_the_windows_key_ends_the_chord():
    submitted = []
    chord = SubmitChord(submit=lambda: submitted.append(True))

    chord.key(LWIN, released=False)
    chord.key(LWIN, released=True)
    chord.key(ENTER, released=False)

    assert submitted == []


def test_the_chord_is_ignored_while_another_window_is_in_front():
    """The hook is global - it sees every keystroke on the machine. Acting on one that wasn't aimed
    at this window would submit the draft while its user types somewhere else entirely."""
    submitted = []
    chord = SubmitChord(submit=lambda: submitted.append(True), focused=lambda: False)

    chord.key(LWIN, released=False)
    chord.key(ENTER, released=False)

    assert submitted == []


@pytest.mark.parametrize("down, up", [(WM_KEYDOWN, WM_KEYUP), (WM_SYSKEYDOWN, WM_SYSKEYUP)])
def test_hook_messages_are_read_as_presses_and_releases(down, up):
    """What the hook hands over is a Windows message, not a press/release flag - and Enter arrives
    under the SYS spelling whenever a modifier is being held down with it."""
    submitted = []
    chord = SubmitChord(submit=lambda: submitted.append(True))

    chord.hook_message(down, LWIN)
    chord.hook_message(down, ENTER)
    chord.hook_message(up, ENTER)
    chord.hook_message(up, LWIN)
    chord.hook_message(down, ENTER)  # the key is no longer held: a plain newline

    assert submitted == [True]


def test_a_faked_release_of_the_windows_key_does_not_end_the_chord():
    """Measured, not supposed: AutoHotkey injects a Windows-key release of its own the moment one
    of its Cmd combinations matches, to keep the Start menu shut. Taking that for a thumb coming
    off the key made "copy something, then submit without letting go" silently do nothing."""
    submitted = []
    chord = SubmitChord(submit=lambda: submitted.append(True))

    chord.hook_message(WM_KEYDOWN, LWIN)
    chord.hook_message(WM_KEYUP, LWIN, injected=True)  # AutoHotkey's disguise, not a real hand
    chord.hook_message(WM_KEYDOWN, ENTER)

    assert submitted == [True]


class FakeWin32:
    """Windows' side of the hook, so the wiring can be tested without touching a real keyboard -
    a global hook installed by the suite would press the Windows key on a live desktop."""

    def __init__(self):
        self.hook = None  # the callback the listener installs
        self.unhooked = False
        self.passed_on = []
        self.quit = threading.Event()
        self.HOOKPROC = lambda callback: callback  # no ctypes wrapper needed for a fake
        self.ctypes = SimpleNamespace(byref=lambda value: value)
        self.wintypes = SimpleNamespace(MSG=lambda: None)
        self.kernel32 = SimpleNamespace(GetCurrentThreadId=lambda: 4242,
                                        GetModuleHandleW=lambda name: 1)
        self.user32 = SimpleNamespace(
            SetWindowsHookExW=self._install,
            UnhookWindowsHookEx=self._uninstall,
            CallNextHookEx=lambda hook, code, message, data: self.passed_on.append((message, data)),
            GetMessageW=lambda message, window, first, last: 0 if self.quit.wait(2) else 0,
            DispatchMessageW=lambda message: None,
            PostThreadMessageW=lambda thread, message, wparam, lparam: self.quit.set(),
        )

    def _install(self, kind, callback, module, thread):
        self.hook = callback
        return 99  # a hook handle

    def _uninstall(self, hook):
        self.unhooked = True

    def key_event(self, data):
        return data, False  # the test hands the key code straight through as the hook's payload


def test_the_hook_feeds_the_chord_and_passes_every_key_on_untouched():
    """Nothing is swallowed, ever: the shell decides whether to open the Start menu by whether a
    key was pressed while the Windows key was held, so eating the Enter opened Search over the
    window. A hook that only watches also cannot break anything else that is typed."""
    submitted = []
    win32 = FakeWin32()
    listener = ChordListener(SubmitChord(submit=lambda: submitted.append(True)),
                             win32=lambda: win32)

    assert listener.start() is True
    win32.hook(0, WM_KEYDOWN, LWIN)
    win32.hook(0, WM_KEYDOWN, ENTER)
    win32.hook(0, WM_KEYDOWN, ord("A"))

    assert submitted == [True]
    assert win32.passed_on == [(WM_KEYDOWN, LWIN), (WM_KEYDOWN, ENTER), (WM_KEYDOWN, ord("A"))]

    listener.stop()
    assert win32.unhooked is True
    assert listener.installed is False
