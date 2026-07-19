"""Keep Windows from giving our child processes their own console windows.

A process launched from a window (pythonw, the Start Menu) has no console of its own, so when it
starts a console program Windows creates a brand-new console window for it - which is how the
Claude CLI the brain runs ended up as a second window on the user's desktop. The SDK spawns it
through `anyio.open_process`, which accepts `creationflags` but is never given any, so the flag has
to be injected at that seam.
"""

CREATE_NO_WINDOW = 0x08000000


def silence_child_consoles(module, attribute="open_process"):
    """Wrap `module.attribute` so every process it starts is windowless unless the caller said
    otherwise. Idempotent-safe to call once at startup, before anything spawns."""
    original = getattr(module, attribute)

    def quietly(*args, **kwargs):
        kwargs.setdefault("creationflags", CREATE_NO_WINDOW)
        return original(*args, **kwargs)

    setattr(module, attribute, quietly)
