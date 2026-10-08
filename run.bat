@echo off
rem One-click runner for Windows: creates a virtual env if missing, installs pandas + matplotlib,
rem then runs a strategy.  Usage:  run.bat                      (runs the example ma_crossover)
rem                                run.bat strategies\mine.py   (runs your file; extra flags are passed through)
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment .venv ...
    py -3 -m venv .venv 2>nul || python -m venv .venv
    if not exist ".venv\Scripts\python.exe" (
        echo Could not create a venv. Install Python 3.10+ from python.org and tick "Add to PATH".
        pause & exit /b 1
    )
)
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt

if "%~1"=="" (
    ".venv\Scripts\python.exe" backtest.py strategies\ma_crossover.py
) else (
    ".venv\Scripts\python.exe" backtest.py %*
)
echo.
pause
