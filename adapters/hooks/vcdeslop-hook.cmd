@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0vcdeslop-hook.ps1" %*
