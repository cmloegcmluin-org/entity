"""Every module imports, and so does everything the windowed entrypoint reaches for.

`pythonw.exe` has no console: an ImportError on the way up is written nowhere and the app simply
does not appear. Double-clicking the shortcut did nothing at all after a class moved modules and
one import in `__main__` was left pointing at where it used to live - no test imported that path,
so the whole suite stayed green while the thing would not start.
"""

import ast
import importlib
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src" / "excephalon"


def _modules():
    return sorted(path.stem for path in SOURCE.glob("*.py") if path.stem != "__init__")


@pytest.mark.parametrize("name", _modules())
def test_every_module_imports(name):
    importlib.import_module(f"excephalon.{name}")


def test_everything_the_window_imports_on_the_way_up_exists():
    """The windowed path imports inside `main()`, so importing the module proves nothing about
    it. Every name it pulls in there is checked here instead."""
    source = ast.parse((SOURCE / "__main__.py").read_text(encoding="utf-8"))
    inside = [node for node in ast.walk(source)
              if isinstance(node, ast.ImportFrom) and node.col_offset > 0
              and node.module and node.module.startswith("excephalon.")]
    assert inside, "the windowed path imports nothing - this test is watching the wrong place"

    for node in inside:
        module = importlib.import_module(node.module)
        for alias in node.names:
            assert hasattr(module, alias.name), f"{node.module} has no {alias.name}"


def test_no_function_reaches_for_a_name_its_module_never_defines():
    """A name that exists in one function and is used in another is a crash waiting for the one
    path that runs it - and it ships green, because the suite never opens a window.

    "Relaunch to upgrade didn't work this time, though actually I can't even open Excephalon":
    the launch died on `session_record`, a name from a different function, and nothing but his
    double-click could have found it. The same scan then found `time` - used by the memory
    nudger's clock, imported nowhere - which had been sitting there unrun.

    Function-level imports are legitimate (the window's heavy ones are deliberately late), so
    every name stored anywhere in the module counts as defined; only names nothing defines at
    all are reported.
    """
    import builtins
    import dis
    from pathlib import Path

    package = Path(__file__).resolve().parents[1] / "src" / "excephalon"
    missing = []
    for path in sorted(package.glob("*.py")):
        code = compile(path.read_text(encoding="utf-8"), str(path), "exec")
        known = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__spec__",
                                      "__package__", "__loader__", "__builtins__"}
        bodies = [code]
        while bodies:
            body = bodies.pop()
            for instruction in dis.get_instructions(body):
                if instruction.opname in ("STORE_NAME", "IMPORT_NAME", "STORE_FAST",
                                          "STORE_GLOBAL", "STORE_DEREF"):
                    known.add(str(instruction.argval).split(".")[0])
            bodies.extend(const for const in body.co_consts if hasattr(const, "co_code"))
        bodies = [code]
        while bodies:
            body = bodies.pop()
            for instruction in dis.get_instructions(body):
                if instruction.opname == "LOAD_GLOBAL" and instruction.argval not in known:
                    missing.append(f"{path.name}: {body.co_name}() uses {instruction.argval!r}")
            bodies.extend(const for const in body.co_consts if hasattr(const, "co_code"))

    assert not missing, "names nothing defines: " + "; ".join(sorted(set(missing)))
