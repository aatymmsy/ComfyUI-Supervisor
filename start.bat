@echo off
setlocal
title ComfyUI Supervisor
pushd "%~dp0" || goto directory_error

echo Starting ComfyUI Supervisor...
echo The first run may take a few minutes to install dependencies.
echo Default address: http://127.0.0.1:7860
echo Keep this window open while the application is running.
echo.

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
set "supervisor_exit=%errorlevel%"
popd
if not "%supervisor_exit%"=="0" (
    echo.
    echo Startup failed. See the error message above.
    pause
)
exit /b %supervisor_exit%

:directory_error
echo Cannot open the project directory.
pause
exit /b 1
