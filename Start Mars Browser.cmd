@echo off
cd /d "%~dp0"
if exist ".python\python.exe" (
  ".python\python.exe" mars_gui.py
) else (
  if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" mars_gui.py
  ) else (
    py -3 mars_gui.py
  )
)
if errorlevel 1 pause
