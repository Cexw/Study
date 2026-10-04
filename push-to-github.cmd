@echo off
chcp 65001 >nul
REM 一键推送到 https://github.com/Cexw/Study
REM 第一次会要求登录：
REM   用户名 -> Cexw
REM   密码   -> GitHub Personal Access Token（不是账号密码）
REM             去 GitHub: Settings > Developer settings > Personal access tokens 生成，勾 repo 权限
setlocal
set GIT=%~dp0.tools\cmd\git.exe
set GIT_SSL_CAINFO=%~dp0.tools-ca\ca-with-steamtools.crt
cd /d "%~dp0"

echo === 当前状态 ===
"%GIT%" log --oneline -3
echo.

"%GIT%" push -u origin main
echo.
echo 退出码: %ERRORLEVEL%
pause
