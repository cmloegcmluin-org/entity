@echo off
rem Double-click, read anything aloud for one minute, and Excephalon learns your voice.
"%~dp0.venv\Scripts\python.exe" -m entity.voiceprint
pause
