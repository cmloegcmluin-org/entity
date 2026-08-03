from types import SimpleNamespace

import pytest

from excephalon import machine
from excephalon.no_console import CREATE_NO_WINDOW, silence_child_consoles


@pytest.fixture
def on_windows(monkeypatch):
    """The desk this was written for. Named rather than assumed, so the wrap is exercised from
    either machine - the Mac's own answer is the no-op below, and neither branch goes untested
    because of which desk the suite happens to be running on."""
    monkeypatch.setattr(machine, "WINDOWS", True)


def test_children_are_started_without_a_console_window(on_windows):
    # Launched from the window there is no console to inherit, so Windows hands each console child
    # its own - the Claude CLI the brain runs turned up as a second window on their desktop.
    calls = []
    module = SimpleNamespace(open_process=lambda *a, **kw: calls.append(kw) or "process")

    silence_child_consoles(module, "open_process")
    module.open_process("claude", stdin=-1)

    assert calls == [{"stdin": -1, "creationflags": CREATE_NO_WINDOW}]


def test_a_caller_that_asks_for_its_own_flags_keeps_them(on_windows):
    calls = []
    module = SimpleNamespace(open_process=lambda *a, **kw: calls.append(kw))

    silence_child_consoles(module, "open_process")
    module.open_process("claude", creationflags=0x10)

    assert calls == [{"creationflags": 0x10}]


def test_a_desk_that_gives_no_child_a_console_is_left_alone(monkeypatch):
    # `creationflags` is a ValueError to a POSIX subprocess, not an ignored nicety: wrapping on
    # the Mac would break every process the app starts - the brain's own CLI first - in the name
    # of a console window that machine never opens.
    monkeypatch.setattr(machine, "WINDOWS", False)
    started = lambda *a, **kw: kw
    module = SimpleNamespace(open_process=started)

    silence_child_consoles(module, "open_process")

    assert module.open_process is started
