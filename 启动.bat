@echo off
setlocal
cd /d "%~dp0"
title BaiLing Launcher

set "MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple"
set "PYW=%~dp0.venv\Scripts\pythonw.exe"

echo ============================================
echo   白绫 BaiLing 启动器（开箱即用）
echo ============================================

REM ---- 1. Python 环境：有 venv 直接用；无则系统 python 建；再无则国内下载安装 ----
if exist "%PYW%" goto :deps
where python >nul 2>nul
if %errorlevel%==0 (
  echo [1/3] 创建虚拟环境...
  python -m venv "%~dp0.venv"
  goto :deps
)
where py >nul 2>nul
if %errorlevel%==0 (
  echo [1/3] 创建虚拟环境...
  py -3 -m venv "%~dp0.venv"
  goto :deps
)
echo [1/3] 未检测到 Python，从国内镜像下载安装...
powershell -NoProfile -Command "(New-Object Net.WebClient).DownloadFile('https://mirrors.huaweicloud.com/python/3.11.9/python-3.11.9-amd64.exe','%TEMP%\py311.exe')"
if not exist "%TEMP%\py311.exe" goto :fail
"%TEMP%\py311.exe" /quiet InstallAllUsers=0 PrependPath=0 Include_pip=1 Include_launcher=1 Shortcuts=0
set "LP=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not exist "%LP%" set "LP=%LOCALAPPDATA%\Programs\Python\Python311-64\python.exe"
if not exist "%LP%" goto :fail
"%LP%" -m venv "%~dp0.venv"
if not exist "%PYW%" goto :fail

:deps
if not exist "%~dp0.venv\.deps_ok" (
  echo [2/3] 安装依赖（国内镜像：%MIRROR%）...
  "%~dp0.venv\Scripts\python.exe" -m pip install -r requirements.txt -i %MIRROR% -q
  if errorlevel 1 goto :fail
  echo ok > "%~dp0.venv\.deps_ok"
) else (
  echo [2/3] 依赖已就绪
)

REM ---- 2. 启动（start 分离，pythonw 后台无窗口，本窗口自动关闭） ----
echo [3/3] 启动白绫...
start "" "%PYW%" "%~dp0webui\server.py"
ping -n 4 127.0.0.1 >nul
start "" "http://127.0.0.1:8765"
echo 白绫已启动：http://127.0.0.1:8765
exit /b 0

:fail
echo [错误] 环境部署失败，请手动安装 Python 3.10 及以上版本后重试。
pause
exit /b 1
