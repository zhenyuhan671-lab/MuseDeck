@echo off
chcp 65001 >nul
title MuseDeck · AI 多账号与 Agent 浏览器指挥台
cd /d "%~dp0"

if exist "MuseDeck.exe" (
    start "" "MuseDeck.exe"
    exit /b 0
)

if exist "dist\MuseDeck.exe" (
    start "" "dist\MuseDeck.exe"
    exit /b 0
)

pythonw main.py 2>nul || python main.py
exit /b 0
