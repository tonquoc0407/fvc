@echo off
REM Windows launcher: starts the tray app with no console window.
cd /d "%~dp0.."
start "" pythonw -m codex_usage tray %*
