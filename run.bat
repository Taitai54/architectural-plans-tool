@echo off
setlocal
cd /d "%~dp0backend"

where py >nul 2>nul && (set PY=py) || (set PY=python)
%PY% --version >nul 2>nul || (
  echo Python was not found. Install Python 3.10+ from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  pause
  exit /b 1
)

echo Closing any copy of the tool that is still running on port 8000...
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { $p = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue; if ($p -and $p.ProcessName -match '^(python|pythonw|py)') { Stop-Process -Id $p.Id -Force } }"
timeout /t 1 >nul

echo Installing requirements (first run takes a minute)...
%PY% -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo Install failed. See the message above.
  pause
  exit /b 1
)

echo.
echo Starting Architectural Plans Tool at http://localhost:8000
echo Leave this window open while you use it. Close it or press Ctrl+C to stop.
echo.
start "" /min cmd /c ""%~dp0open-browser.bat""
%PY% -m uvicorn main:app --host 127.0.0.1 --port 8000
pause
