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

SOURCE = Path(__file__).resolve().parents[1] / "src" / "entity"


def _modules():
    return sorted(path.stem for path in SOURCE.glob("*.py") if path.stem != "__init__")


@pytest.mark.parametrize("name", _modules())
def test_every_module_imports(name):
    importlib.import_module(f"entity.{name}")


def test_everything_the_window_imports_on_the_way_up_exists():
    """The windowed path imports inside `main()`, so importing the module proves nothing about
    it. Every name it pulls in there is checked here instead."""
    source = ast.parse((SOURCE / "__main__.py").read_text(encoding="utf-8"))
    inside = [node for node in ast.walk(source)
              if isinstance(node, ast.ImportFrom) and node.col_offset > 0
              and node.module and node.module.startswith("entity.")]
    assert inside, "the windowed path imports nothing - this test is watching the wrong place"

    for node in inside:
        module = importlib.import_module(node.module)
        for alias in node.names:
            assert hasattr(module, alias.name), f"{node.module} has no {alias.name}"
