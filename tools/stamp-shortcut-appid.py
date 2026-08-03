"""Stamp every Excephalon shortcut with the app's AppUserModelID, so a pin and the running window are
the same taskbar button.

Windows matches a shortcut to a running window by AppUserModelID. Excephalon process declares one
(excephalon.mirror.APP_ID); without the same id on the .lnk, launching it gives a second, separate
button - the pin sitting inert while the running app lights up somewhere else entirely, and
pinning THAT one pins the interpreter, generic icon and all.

Both copies have to carry it. Pinning does not link to the Start Menu shortcut, it takes a COPY of
it, and that copy keeps whatever id it was made with - so a pin made before this ran, or before the
id last changed, still holds the old one. Run after install-start-menu.ps1, after re-pinning, and
whenever APP_ID changes.
"""

import sys
from pathlib import Path

import pythoncom
from win32com.propsys import propsys, pscon
from win32com.shell import shell

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from excephalon.mirror import APP_ID  # noqa: E402  - the one definition of the id, not a copy of it

STGM_READWRITE = 0x00000002  # loading a .lnk read-only makes every write Access Denied

# Where Windows keeps the two copies of a shortcut: the one in the menu, and the one a pin made.
# Both names, because a pin is a copy and keeps the name it was made under - a pin from before
# the app presented as Excephalon still sits there as Excephalon.lnk, and it needs the id no less.
_MENU = Path.home() / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs"
_PINS = Path.home() / "AppData/Roaming/Microsoft/Internet Explorer/Quick Launch/User Pinned/TaskBar"
SHORTCUTS = tuple(place / name for place in (_MENU, _PINS)
                  for name in ("Excephalon.lnk", "Excephalon.lnk"))


def stamp(path, app_id):
    link = pythoncom.CoCreateInstance(
        shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink
    )
    persist = link.QueryInterface(pythoncom.IID_IPersistFile)
    persist.Load(str(path), STGM_READWRITE)
    store = link.QueryInterface(propsys.IID_IPropertyStore)
    store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(app_id, pythoncom.VT_LPWSTR))
    store.Commit()
    persist.Save(str(path), 0)


def read_back(path):
    link = pythoncom.CoCreateInstance(
        shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink
    )
    link.QueryInterface(pythoncom.IID_IPersistFile).Load(str(path), 0)
    store = link.QueryInterface(propsys.IID_IPropertyStore)
    return store.GetValue(pscon.PKEY_AppUserModel_ID).GetValue()


if __name__ == "__main__":
    # An explicit path wins; otherwise both copies, and one that isn't there is simply said so -
    # they may have pinned it and not put it in the Start Menu, or the other way round.
    wanted = [Path(sys.argv[1])] if len(sys.argv) > 1 else list(SHORTCUTS)
    for shortcut in wanted:
        if not shortcut.exists():
            print(f"{shortcut} - not there, nothing to stamp")
            continue
        stamp(shortcut, APP_ID)
        print(f"{shortcut} now declares AppUserModelID={read_back(shortcut)!r}")
