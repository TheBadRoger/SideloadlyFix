@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0prepare.ps1" %*
if errorlevel 1 pause
