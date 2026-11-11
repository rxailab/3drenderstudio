@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
python -c "import flask,PIL" >nul 2>nul
if errorlevel 1 python -m pip install -r requirements.txt
echo 物影 Studio 即将启动，请在浏览器打开 http://127.0.0.1:8765
python app.py
pause
