@echo off
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" "desktop_voice_manager.py"
) else if exist ".venv\Scripts\python.exe" (
  start "" ".venv\Scripts\python.exe" "desktop_voice_manager.py"
) else (
  start "" python "desktop_voice_manager.py"
)
