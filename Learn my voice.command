#!/bin/bash
# Double-click, read anything aloud for one minute, and Excephalon learns your voice.
# (The Mac's "Learn my voice.bat": a .command is what Finder will run on a double-click.)
cd "$(dirname "$0")"
.venv/bin/python -m entity.voiceprint
echo
read -n 1 -s -r -p "Press any key to close."
