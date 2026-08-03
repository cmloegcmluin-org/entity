#!/bin/bash
# Double-click, sign in to Google once, and Excephalon's errands can reach Gmail and Calendar.
cd "$(dirname "$0")"
".venv/bin/python" -m excephalon.google_bridge --connect
echo
read -p "Press Enter to close..."
