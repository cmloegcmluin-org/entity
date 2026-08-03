#!/bin/bash
# Put "Excephalon" in /Applications, so the Mac launches it the way the Start Menu shortcut
# launches it on the desk: an icon to click, a name under it, and its own thing in the Dock.
# Run from anywhere; everything is derived from this script's own location. Re-run after moving
# the repo. Run it once after cloning; it creates the bundle, nothing else is needed.
#
# /Applications rather than ~/Applications, because that is "where literally every other
# Application on my computer lives" - a second Applications folder most Macs never show him is a
# place to lose an app, not a place to find one. It is admin-writable, so no password is wanted.
#
# The bundle is not decoration. macOS attributes a microphone permission to the APPLICATION that
# asked, so a bare `python -m excephalon --gui` asks on behalf of whatever terminal started it - the
# permission lands on Terminal, the prompt names Terminal, and revoking it later means hunting
# for Excephalon in a list that never mentions it. Inside a bundle the ask is Excephalon's own,
# with the reason below in the dialog, and it is Excephalon that appears under Privacy & Security.
set -e

repo=$(cd "$(dirname "$0")/.." && pwd)
app="/Applications/Excephalon.app"

python="$repo/.venv/bin/python"
if [ ! -x "$python" ]; then
    echo "no interpreter at $python - create the venv first (see the README)" >&2
    exit 1
fi

rm -rf "$app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"

# The icon, from the same 256px source the README and the Windows .ico are cut from. `iconutil`
# wants a folder of named sizes; `sips` makes them. Both ship with macOS - nothing to install.
iconset=$(mktemp -d)/excephalon.iconset
mkdir -p "$iconset"
for size in 16 32 128 256 512; do
    sips -z $size $size "$repo/assets/excephalon.png" \
        --out "$iconset/icon_${size}x${size}.png" >/dev/null
    sips -z $((size * 2)) $((size * 2)) "$repo/assets/excephalon.png" \
        --out "$iconset/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$iconset" -o "$app/Contents/Resources/excephalon.icns"
rm -rf "$(dirname "$iconset")"

# A shell script, not the interpreter itself: the bundle has to keep working when the venv is
# rebuilt, and it has to run from the repo so `runtime/` is where the app expects it.
cat > "$app/Contents/MacOS/Excephalon" <<EOF
#!/bin/bash
cd "$repo"
exec "$python" -m excephalon --gui
EOF
chmod +x "$app/Contents/MacOS/Excephalon"

cat > "$app/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>Excephalon</string>
    <key>CFBundleDisplayName</key><string>Excephalon</string>
    <key>CFBundleExecutable</key><string>Excephalon</string>
    <key>CFBundleIdentifier</key><string>Excephalon.VoiceCompanion</string>
    <key>CFBundleIconFile</key><string>excephalon</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>0.1.0</string>
    <key>NSHighResolutionCapable</key><true/>
    <!-- What the permission dialog says. It is read aloud to nobody but it is the whole
         explanation he gets at the moment macOS asks, so it says what listening is FOR. -->
    <key>NSMicrophoneUsageDescription</key>
    <string>Excephalon listens so you can talk to it.</string>
</dict>
</plist>
EOF

# A bundle whose Info.plist changed under a name the system has already seen keeps the old one
# until the cache is told otherwise - which is how a fresh icon stays the stale one.
touch "$app"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
    -f "$app" 2>/dev/null || true

echo "installed $app"
