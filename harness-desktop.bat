@echo off
cd /d "%~dp0"
uv run harness desktop %*
if errorlevel 1 pause
