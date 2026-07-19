from types import SimpleNamespace

from entity.no_console import CREATE_NO_WINDOW, silence_child_consoles


def test_children_are_started_without_a_console_window():
    # Launched from the window there is no console to inherit, so Windows hands each console child
    # its own - the Claude CLI the brain runs turned up as a second window on their desktop.
    calls = []
    module = SimpleNamespace(open_process=lambda *a, **kw: calls.append(kw) or "process")

    silence_child_consoles(module, "open_process")
    module.open_process("claude", stdin=-1)

    assert calls == [{"stdin": -1, "creationflags": CREATE_NO_WINDOW}]


def test_a_caller_that_asks_for_its_own_flags_keeps_them():
    calls = []
    module = SimpleNamespace(open_process=lambda *a, **kw: calls.append(kw))

    silence_child_consoles(module, "open_process")
    module.open_process("claude", creationflags=0x10)

    assert calls == [{"creationflags": 0x10}]
