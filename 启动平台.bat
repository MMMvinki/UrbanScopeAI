@echo off
chcp 65001 >nul
title UrbanScope 平台一键启动
echo ========================================
echo   UrbanScope 城市数据分析平台
echo   正在启动后端(8011) + 前端(5175)...
echo ========================================
echo.

rem 自动停掉占用 8011 的旧后端进程，避免端口冲突（改代码后双击即可干净重启）
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8011" ^| findstr "LISTENING"') do (
    echo 正在停止旧后端进程 PID=%%p ...
    taskkill /F /PID %%p >nul 2>nul
)

cd /d "%~dp0apps\api"
start "UrbanScope-API(8011)" cmd /k "chcp 65001 >nul & .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8011"

cd /d "%~dp0apps\web"
start "UrbanScope-Web(5175)" cmd /k "npm run dev"

echo.
echo 正在等待服务启动...
timeout /t 6 /nobreak >nul
start "" http://localhost:5175

echo.
echo 两个窗口已打开，浏览器已自动打开 http://localhost:5175
echo 若页面没自动刷新，按 F5 刷新即可。
echo.
pause
