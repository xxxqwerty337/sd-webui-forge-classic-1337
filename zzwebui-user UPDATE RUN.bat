@echo off
setlocal
cd /d "%~dp0"

:: Optional overrides:
:: set "PYTHON="
:: set "GIT="
:: set "VENV_DIR="

set "COMMANDLINE_ARGS="

:: Allow normal startup checks and dependency installation.
:: Run this after updating Forge or its extensions.
call webui.bat --uv --forge-ref-comfy-home "D:\ComfyUI\ComfyUI\models"

endlocal