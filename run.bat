@echo off
setlocal
cd /d "%~dp0backend"

where py >nul 2>nul && (set PY=py) || (set PY=python)
%PY% --version >nul 2>nul || (
  echo Python was not found. Install Python 3.10+ from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  pause
  exit /b 1
)

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
start "" /min cmd /c "timeout /t 3 >nul & start http://localhost:8000"
%PY% -m uvicorn main:app --host 127.0.0.1 --port 8000
pause
