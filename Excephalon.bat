@echo off
rem Double-click to run the Entity in its window - no terminal needed.
start "" "%~dp0.venv\Scripts\pythonw.exe" -m entity --gui
