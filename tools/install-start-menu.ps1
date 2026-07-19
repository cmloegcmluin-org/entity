# Put "Entity" in the Start Menu: a shortcut to pythonw -m entity --gui with the repo's icon.
# Run from anywhere; everything is derived from this script's own location. Re-run after moving
# the repo. (Agents run this; the user just clicks the Start Menu entry it creates.)
$repo = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $repo ".venv\Scripts\pythonw.exe"
$icon = Join-Path $repo "assets\entity.ico"
$programs = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$shortcut = Join-Path $programs "Entity.lnk"

$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($shortcut)
$link.TargetPath = $pythonw
$link.Arguments = "-m entity --gui"
$link.WorkingDirectory = $repo
$link.IconLocation = $icon
$link.Description = "Entity - voice companion"
$link.Save()
Write-Output "installed $shortcut"
