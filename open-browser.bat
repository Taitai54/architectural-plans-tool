@echo off
rem Waits for the server, then opens the tool in Chrome if it is installed, otherwise in the default browser.
timeout /t 3 >nul
start "" chrome "http://localhost:8000" 2>nul
if errorlevel 1 start "" "http://localhost:8000"
