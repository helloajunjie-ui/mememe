@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo ============================================
echo   白绫 Bailing Web 界面启动
echo   http://127.0.0.1:8765
echo   关闭窗口 = 停止服务
echo ============================================
".venv\Scripts\python.exe" webui\server.py
pause
