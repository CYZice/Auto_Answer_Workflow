@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion

echo ===========================================
echo    Zyb-Agent 一键启动脚本 (前端 + 后端)
echo ===========================================

set PROJECT_ROOT=%CD%

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

:: 进入后端目录
cd "%PROJECT_ROOT%\backend"

echo 启动后端服务 (端口 8080)...
:: 优先使用项目根目录下的 .venv 虚拟环境
if exist "%PROJECT_ROOT%\.venv\Scripts\activate.bat" (
    echo 检测到 .venv 虚拟环境，使用该环境启动...
    start "Zyb-Agent Backend" cmd /c "call "%PROJECT_ROOT%\.venv\Scripts\activate.bat" && uvicorn app.main:app --reload --port 8080"
) else (
    echo 警告: 未检测到 .venv 虚拟环境，尝试使用全局环境启动...
    start "Zyb-Agent Backend" cmd /c "uvicorn app.main:app --reload --port 8080"
)

echo.
echo ^>^>^> 准备启动前端 (Vite/React)...
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
