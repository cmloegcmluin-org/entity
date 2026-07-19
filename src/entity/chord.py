"""The modifier beside the spacebar, held, then Enter: submit the draft.

On a Mac keyboard reaching Windows through a KVM that modifier is Cmd, arriving as the Windows
key - and Win+Enter cannot be had by any ordinary means. Every line of that was measured, not
guessed:

  * `RegisterHotKey(MOD_WIN, VK_RETURN)` is refused with ERROR_HOTKEY_ALREADY_REGISTERED (1409),
    as are Win+Ctrl+Enter and Win+Shift+Enter, while Win+Alt+Enter and Win+J are granted - so
    something already owns the Win+Enter family and no window can register it.
  * With the chord pressed, a low-level hook logged Enter's key-DOWN while the window received
    only its key-UP: that owner eats the key-down before any window sees it.
  * A Mac-keyboard remapping script (AutoHotkey) holds the Windows key as a prefix for its
    Cmd-style bindings, so the Windows key itself doesn't reach a window either.

A WH_KEYBOARD_LL hook sits ahead of all of that - it is the first thing in the input path, before
hotkey dispatch and before any hook installed earlier. AutoHotkey's went in at boot and ours when
the Entity starts, which is later, and low-level hooks run newest-first; injecting AutoHotkey's own
`LWin & c` and watching our hook log the `c` confirmed the order. So the hook is not a workaround
here; it is the only mechanism that can see this chord at all.

It only ever watches. Swallowing the Enter was tried and measured worse: the shell decides whether
to open the Start menu by whether any key was pressed between the Windows key going down and
coming up, so eating the Enter hid the evidence and Search opened over the window every time (a
synthetic disguise keystroke did not stop it). Passed through, the key-down keeps the Start menu
shut and is eaten downstream anyway, so no newline reaches the draft - and a global hook that never
alters the key stream cannot break anything else that is typed. If that downstream owner ever goes away,
the cost is a newline typed into a draft that was just emptied.

Being global, the hook sees every keystroke on the machine, so `SubmitChord` acts only while a
window of this process is in front.
"""

import functools
import threading

LWIN, RWIN, ENTER = 0x5B, 0x5C, 0x0D
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
WM_QUIT = 0x0012
WH_KEYBOARD_LL = 13
LLKHF_INJECTED = 0x10  # this key was synthesized by a program, not pressed by a hand
RELEASES = (WM_KEYUP, WM_SYSKEYUP)
HOOK_INSTALL_TIMEOUT = 2.0


class SubmitChord:
    """Raw key events in, `submit` called when the chord completes. Pure, so every rule above can
    be tested without a keyboard, a window or a hook."""

    def __init__(self, submit, focused=lambda: True):
        self._submit = submit
        self._focused = focused
        self._held = set()
        self._claimed = False  # this Enter press was ours, so its repeats aren't a second submit

    def hook_message(self, message, code, injected=False):
        """A low-level keyboard hook's own vocabulary: a window message and a virtual key code."""
        self.key(code, released=message in RELEASES, injected=injected)

    def key(self, code, *, released, injected=False):
        if code in (LWIN, RWIN):
            if not released:
                self._held.add(code)
            elif not injected:
                # Only a real hand ends the chord. AutoHotkey fakes a Windows-key release when one
                # of its Cmd combinations fires, and taking that at face value lost every submit
                # made without letting go of the key first - measured, then fixed.
                self._held.discard(code)
        elif code == ENTER:
            if released:
                self._claimed = False
            elif self._held and not self._claimed and self._focused():
                self._claimed = True
                self._submit()


class Win32:
    """The handful of calls the hook needs, with their signatures declared.

    Every handle-returning call gets an explicit restype: ctypes defaults to a 32-bit int, which
    silently truncates a 64-bit HMODULE - that alone made the first hook fail with MOD_NOT_FOUND.
    """

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self.ctypes, self.wintypes = ctypes, wintypes
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
                                           wintypes.WPARAM, wintypes.LPARAM)

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]

        self.KBDLLHOOKSTRUCT = KBDLLHOOKSTRUCT
        self.kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        self.kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        self.kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        self.kernel32.GetCurrentProcessId.restype = wintypes.DWORD
        self.user32.SetWindowsHookExW.argtypes = [ctypes.c_int, self.HOOKPROC,
                                                  wintypes.HMODULE, wintypes.DWORD]
        self.user32.SetWindowsHookExW.restype = wintypes.HHOOK
        self.user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        self.user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int,
                                               wintypes.WPARAM, wintypes.LPARAM]
        self.user32.CallNextHookEx.restype = ctypes.c_ssize_t
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, wintypes.LPDWORD]
        self.user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT,
                                                   wintypes.WPARAM, wintypes.LPARAM]

    def key_event(self, data):
        """The virtual key code, and whether some other program synthesized this press."""
        event = self.ctypes.cast(data, self.ctypes.POINTER(self.KBDLLHOOKSTRUCT)).contents
        return event.vkCode, bool(event.flags & LLKHF_INJECTED)

    def foreground_is_ours(self):
        """Is the window in front one of ours? Asked by process rather than by window handle, so
        the hook's thread can answer it without touching Tk from off the Tk thread."""
        owner = self.wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(self.user32.GetForegroundWindow(),
                                             self.ctypes.byref(owner))
        return owner.value == self.kernel32.GetCurrentProcessId()


@functools.cache
def _shared_win32():
    """One set of declared signatures for the whole process - building them again on each keystroke
    would put two DLL loads inside a hook callback Windows expects back within milliseconds."""
    return Win32()


def foreground_is_ours():
    """The focus test as a plain callable, for handing to a SubmitChord."""
    try:
        return _shared_win32().foreground_is_ours()
    except Exception:
        return False


class ChordListener:
    """Runs a `SubmitChord` against the real keyboard, on a thread of its own.

    The hook needs a message loop to feed it, and one that always answers promptly: Windows drops a
    low-level hook whose thread doesn't return within `LowLevelHooksTimeout`. So it gets a bare
    pump thread rather than Tk's, which is busy rendering and can stall.
    """

    def __init__(self, chord, win32=Win32):
        self._chord = chord
        self._win32_class = win32
        self._win32 = None
        self._thread = None
        self._thread_id = None
        self._callback = None  # a garbage-collected hook procedure is a crash, so hold it here
        self.installed = False

    def start(self):
        """Install the hook and say whether it took - a keyboard shortcut that can't be had must
        never keep the window from opening."""
        ready = threading.Event()
        self._thread = threading.Thread(target=self._pump, args=(ready,), daemon=True,
                                        name="submit-chord")
        self._thread.start()
        ready.wait(HOOK_INSTALL_TIMEOUT)
        return self.installed

    def _pump(self, ready):
        try:
            self._win32 = win32 = self._win32_class()
        except Exception:
            ready.set()  # not Windows: no chord, and nothing else disturbed
            return

        def on_key(code, message, data):
            if code >= 0:
                self._chord.hook_message(message, *win32.key_event(data))
            return win32.user32.CallNextHookEx(None, code, message, data)

        self._callback = win32.HOOKPROC(on_key)
        self._thread_id = win32.kernel32.GetCurrentThreadId()
        hook = win32.user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._callback,
                                              win32.kernel32.GetModuleHandleW(None), 0)
        self.installed = bool(hook)
        ready.set()
        if not hook:
            return
        try:
            message = win32.wintypes.MSG()
            while win32.user32.GetMessageW(win32.ctypes.byref(message), None, 0, 0) > 0:
                win32.user32.DispatchMessageW(win32.ctypes.byref(message))
        finally:
            win32.user32.UnhookWindowsHookEx(hook)
            self.installed = False

    def stop(self):
        """Ask the pump to end, so the hook is unhooked rather than left on a dead thread."""
        if self._thread is None or self._win32 is None or self._thread_id is None:
            return
        self._win32.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self._thread.join(timeout=HOOK_INSTALL_TIMEOUT)
