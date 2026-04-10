@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion

echo ===========================================
echo    Zyb-Agent 一键启动脚本 (前端 + 后端)
echo ===========================================

set PROJECT_ROOT=%CD%

goto :main

:: 按端口清理占用进程
:kill_port_process
set "TARGET_PORT=%~1"
set "FOUND_PID="
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /R /C:":%TARGET_PORT% .*LISTENING"') do (
    set "FOUND_PID=%%a"
    if not "%%a"=="0" (
        echo 检测到端口 %TARGET_PORT% 被 PID %%a 占用，正在结束进程...
        taskkill /PID %%a /F >nul 2>nul
    )
)

if not defined FOUND_PID (
    goto :eof
)

:: 二次检查，避免进程未完全退出
timeout /t 1 /nobreak >nul
set "FOUND_PID="
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /R /C:":%TARGET_PORT% .*LISTENING"') do (
    set "FOUND_PID=%%a"
)
if defined FOUND_PID (
    echo 警告: 端口 %TARGET_PORT% 仍被占用，请手动检查相关进程。
)
goto :eof

:main

:: 检查 npm
where npm >nul 2>nul
if %errorlevel% neq 0 (
    echo 错误: 未找到 npm，请先安装 Node.js
    pause
    exit /b 1
)

:: 检查 python
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo 错误: 未找到 python，请先安装 Python
    pause
    exit /b 1
)

echo.
echo ^>^>^> 准备启动后端 (FastAPI)...

:: 清理后端端口占用，避免重复启动
call :kill_port_process 8080

:: 默认启用真实模式（可由外部环境变量覆盖）
if "%AUTOMATION_USE_MOCK%"=="" set AUTOMATION_USE_MOCK=0
if "%AUTOMATION_SKIP_BROWSER_INSTALL%"=="" set AUTOMATION_SKIP_BROWSER_INSTALL=1
if "%AUTOMATION_BROWSER_CHANNEL%"=="" set AUTOMATION_BROWSER_CHANNEL=chrome
if "%AUTOMATION_TARGET_URL%"=="" set AUTOMATION_TARGET_URL=https://yy.xuejie.cn/#/login
if "%AUTOMATION_BACKEND_RELOAD%"=="" set AUTOMATION_BACKEND_RELOAD=0

echo 自动化模式: AUTOMATION_USE_MOCK=%AUTOMATION_USE_MOCK%
if not "%AUTOMATION_TARGET_URL%"=="" echo 目标地址: %AUTOMATION_TARGET_URL%
echo 浏览器通道: %AUTOMATION_BROWSER_CHANNEL%
echo 跳过浏览器下载: AUTOMATION_SKIP_BROWSER_INSTALL=%AUTOMATION_SKIP_BROWSER_INSTALL%
echo 后端热重载: AUTOMATION_BACKEND_RELOAD=%AUTOMATION_BACKEND_RELOAD%

set "UVICORN_RELOAD_ARG="
if "%AUTOMATION_BACKEND_RELOAD%"=="1" (
    set "UVICORN_RELOAD_ARG=--reload"
)

:: 进入后端目录
cd "%PROJECT_ROOT%\backend"

:: 安装 Playwright Chromium（AUTOMATION_SKIP_BROWSER_INSTALL=1 可跳过）
if "%AUTOMATION_SKIP_BROWSER_INSTALL%"=="1" (
    echo 已设置 AUTOMATION_SKIP_BROWSER_INSTALL=1，跳过浏览器安装。
) else (
    echo 检查 Playwright Chromium 安装状态...
    if exist "%PROJECT_ROOT%\.venv\Scripts\activate.bat" (
        call "%PROJECT_ROOT%\.venv\Scripts\activate.bat"
    )
    python -m playwright install chromium >nul 2>nul
)

echo 启动后端服务 (端口 8080)...
:: 优先使用项目根目录下的 .venv 虚拟环境
if exist "%PROJECT_ROOT%\.venv\Scripts\activate.bat" (
    echo 检测到 .venv 虚拟环境，使用该环境启动...
    start "Zyb-Agent Backend" cmd /c "call "%PROJECT_ROOT%\.venv\Scripts\activate.bat" && uvicorn app.main:app %UVICORN_RELOAD_ARG% --port 8080"
) else (
    echo 警告: 未检测到 .venv 虚拟环境，尝试使用全局环境启动...
    start "Zyb-Agent Backend" cmd /c "uvicorn app.main:app %UVICORN_RELOAD_ARG% --port 8080"
)

echo.
echo ^>^>^> 准备启动前端 (Vite/React)...

:: 清理前端端口占用，避免重复启动
call :kill_port_process 5173

cd "%PROJECT_ROOT%\frontend"

:: 检查 node_modules
if not exist "node_modules" (
    echo 未找到 node_modules，正在安装前端依赖...
    call npm install
)

:: 启动前端服务
echo 启动前端服务...
start "Zyb-Agent Frontend" cmd /c "npm run dev"

echo.
echo ===========================================
echo   服务已分别在新的命令行窗口中启动！
echo   前端页面: http://localhost:5173
echo   后端 API: http://localhost:8080
echo ===========================================
echo.
echo (直接关闭弹出的两个命令行窗口即可停止服务)
echo.
pause

cd "%PROJECT_ROOT%"
endlocal
