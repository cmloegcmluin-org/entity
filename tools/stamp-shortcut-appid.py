"""Stamp the Start Menu shortcut with Entity's AppUserModelID, so a pin and the running window
are the same taskbar button.

Windows matches a pinned shortcut to a running window by AppUserModelID. The Entity process
declares one (entity.gui.APP_ID); without the same id on the .lnk, pinning it would give him a
second, separate button - the running app lighting up somewhere else entirely, which is the bug he
just had. Run once after install-start-menu.ps1, and again if that shortcut is ever recreated.
"""

import sys
from pathlib import Path

import pythoncom
from win32com.propsys import propsys, pscon
from win32com.shell import shell

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from entity.gui import APP_ID  # noqa: E402  - the one definition of the id, not a copy of it

STGM_READWRITE = 0x00000002  # loading a .lnk read-only makes every write Access Denied

SHORTCUT = Path(sys.argv[1]) if len(sys.argv) > 1 else (
    Path.home() / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Entity.lnk"
)


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
    stamp(SHORTCUT, APP_ID)
    print(f"{SHORTCUT} now declares AppUserModelID={read_back(SHORTCUT)!r}")
