@echo off
rem Launches the SLATE v2 content editor. Run from anywhere -- it cd's to
rem its own location first. Shares the same virtual environment as
rem run_game.bat (creates one on first run if it doesn't exist yet).
setlocal
cd /d "%~dp0"

if not exist .venv (
    echo First run: setting up a virtual environment and installing dependencies...
    python -m venv .venv
    if errorlevel 1 (
        echo Could not create a virtual environment. Is Python 3.11+ installed and on PATH?
        pause
        exit /b 1
    )
    call .venv\Scripts\activate.bat
    python -m pip install --upgrade pip
    python -m pip install -e ".[dev]"
) else (
    call .venv\Scripts\activate.bat
)

python run_editor.py
if errorlevel 1 (
    echo.
    echo The editor exited with an error -- see above.
    pause
)
