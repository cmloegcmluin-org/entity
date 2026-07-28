# Put "Excephalon" in the Start Menu: a shortcut to pythonw -m entity --gui with the repo's icon.
# Run from anywhere; everything is derived from this script's own location. Re-run after moving
# the repo. Run it once after cloning; it creates the entry, nothing else is needed.
$repo = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $repo ".venv\Scripts\pythonw.exe"
$icon = Join-Path $repo "assets\excephalon.ico"
$programs = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$shortcut = Join-Path $programs "Excephalon.lnk"

$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($shortcut)
$link.TargetPath = $pythonw
$link.Arguments = "-m entity --gui"
$link.WorkingDirectory = $repo
$link.IconLocation = $icon
$link.Description = "Excephalon - voice companion"
$link.Save()
Write-Output "installed $shortcut"

# Stamp the shortcuts with the same AppUserModelID the app declares, so pinning one and running it
# are the SAME taskbar button - without this a pin sits inert while the running window lights up
# somewhere else. Recreating the shortcut drops the id, which is why this runs right here. No
# argument, so it does the pinned copy too: a pin is a COPY and keeps the id it was made with.
& (Join-Path $repo ".venv\Scripts\python.exe") (Join-Path $PSScriptRoot "stamp-shortcut-appid.py")
