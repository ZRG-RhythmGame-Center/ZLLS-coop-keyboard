@echo off
REM 以项目根目录为工作目录启动 Receiver，供开机自启或任务计划使用。
set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%.."
uv run zlls-coop-keyboard-receiver %*
pause
