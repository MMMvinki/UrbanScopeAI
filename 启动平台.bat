@echo off
chcp 65001 >nul
title UrbanScope 平台一键启动
echo ========================================
echo   UrbanScope 城市数据分析平台
echo   正在启动后端(8011) + 前端(5175)...
echo ========================================
echo.

cd /d "%~dp0apps\api"
start "UrbanScope-API(8011)" cmd /k ".venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8011"

cd /d "%~dp0apps\web"
start "UrbanScope-Web(5175)" cmd /k "npm run dev"

echo.
echo 两个窗口已打开（别关它们，关掉服务就停了）。
echo 等窗口出现后，浏览器访问： http://localhost:5175
echo 若没自动刷新，按 F5 刷新页面即可。
echo.
pause
