@echo off
rem Pit lap viewer: double-click to start. The first run sets up Python packages (needs internet once).
setlocal
cd /d "%~dp0"
set "VENV=%~dp0.venv"
set "PY=%VENV%\Scripts\python.exe"

if exist "%VENV%\ready.txt" goto run

echo Setting up the lap viewer. This needs internet the first time and takes a few minutes.
set "BASEPY="
where py >nul 2>nul
if %errorlevel%==0 set "BASEPY=py -3"
if not defined BASEPY (
  where python >nul 2>nul
  if not errorlevel 1 set "BASEPY=python"
)
if not defined BASEPY goto nopython

if exist "%VENV%" rmdir /s /q "%VENV%"
%BASEPY% -m venv "%VENV%"
if errorlevel 1 goto nopython
"%PY%" -m pip install --upgrade pip
"%PY%" -m pip install -r "%~dp0..\requirements.txt"
if errorlevel 1 goto pipfail
echo ok> "%VENV%\ready.txt"

:run
"%PY%" -I "%~dp0lapviewer.py" %*
if errorlevel 1 pause
exit /b

:nopython
echo.
echo Python 3.10 to 3.14 (64-bit) is needed. Install it from https://www.python.org/downloads/windows/
echo then double-click this file again.
pause
exit /b 1

:pipfail
echo.
echo Installing the Python packages failed. Check the internet connection and run this file again.
if exist "%VENV%" rmdir /s /q "%VENV%"
pause
exit /b 1
