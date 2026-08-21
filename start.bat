@echo off
chcp 65001 >nul
title 이터널 리턴 실시간 점수판
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 pause
