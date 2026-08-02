@echo off
rem Double-click to run Excephalon in its window - no terminal needed.
start "" "%~dp0.venv\Scripts\pythonw.exe" -m entity --gui
