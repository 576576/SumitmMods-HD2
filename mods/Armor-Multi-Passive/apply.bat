@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo Armor Multi-Passive - apply queued selection
echo --------------------------------------------
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo Python not found. Install Python 3.10+ with "Add to PATH" checked.
    echo.
    pause
    exit /b 1
)

python "tools\apply_pending.py" %*
set RC=%errorlevel%

echo.
if %RC%==0  echo Done. You can start the game now.
if %RC%==2  echo Aborted: path or state file problem, see the message above.
if %RC%==3  echo Aborted: the game is still running. Exit it fully and retry.
if %RC%==4  echo Aborted: the game regenerated this file. Add --force to overwrite it.
if %RC%==5  echo Aborted: merge failed, see the message above.
if %RC%==6  echo Aborted: write verification failed, original is kept.
echo.
pause
exit /b %RC%
