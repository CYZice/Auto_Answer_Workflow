#!/bin/bash

# 颜色输出配置
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

kill_port_process() {
    local port="$1"
    local pids
    pids=$(ss -ltnp 2>/dev/null | grep -E ":${port}\\b" | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u)
    if [ -z "$pids" ]; then
        return 0
    fi

    echo -e "${YELLOW}检测到端口 ${port} 已被占用，准备清理旧进程: ${pids}${NC}"
    for pid in $pids; do
        kill "$pid" 2>/dev/null || true
    done

    # 等待进程优雅退出；若仍占用端口则升级为 SIGKILL
    sleep 1
    if ss -ltnp 2>/dev/null | grep -E ":${port}\\b" >/dev/null; then
        pids=$(ss -ltnp 2>/dev/null | grep -E ":${port}\\b" | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u)
        if [ -n "$pids" ]; then
            echo -e "${YELLOW}端口 ${port} 仍被占用，强制结束进程: ${pids}${NC}"
            for pid in $pids; do
                kill -9 "$pid" 2>/dev/null || true
            done
            sleep 1
        fi
    fi
}

echo -e "${BLUE}===========================================${NC}"
echo -e "${BLUE}   Zyb-Agent 一键启动脚本 (前端 + 后端)    ${NC}"
echo -e "${BLUE}===========================================${NC}"

# 获取当前脚本所在目录
PROJECT_ROOT=$(pwd)

# 检查是否安装了必要的依赖
if ! command -v npm &> /dev/null; then
    echo -e "${RED}错误: 未找到 npm，请先安装 Node.js${NC}"
    exit 1
fi

if ! command -v python3 &> /dev/null; then
    echo -e "${RED}错误: 未找到 python3，请先安装 Python 3${NC}"
    exit 1
fi

# 1. 启动后端
echo -e "\n${YELLOW}>>> 准备启动后端 (FastAPI)...${NC}"
cd "${PROJECT_ROOT}" || exit

# 清理端口占用，避免重复启动导致 Address already in use
kill_port_process 8080

# 默认启用真实模式（可由外部环境变量覆盖）
export AUTOMATION_USE_MOCK=${AUTOMATION_USE_MOCK:-0}
export AUTOMATION_SKIP_BROWSER_INSTALL=${AUTOMATION_SKIP_BROWSER_INSTALL:-1}
export AUTOMATION_BROWSER_CHANNEL=${AUTOMATION_BROWSER_CHANNEL:-chrome}
export AUTOMATION_TARGET_URL=${AUTOMATION_TARGET_URL:-https://yy.xuejie.cn/#/login}

echo -e "${GREEN}自动化模式: AUTOMATION_USE_MOCK=${AUTOMATION_USE_MOCK}${NC}"
if [ -n "${AUTOMATION_TARGET_URL}" ]; then
    echo -e "${GREEN}目标地址: ${AUTOMATION_TARGET_URL}${NC}"
fi
echo -e "${GREEN}浏览器通道: ${AUTOMATION_BROWSER_CHANNEL}${NC}"
echo -e "${GREEN}跳过浏览器下载: AUTOMATION_SKIP_BROWSER_INSTALL=${AUTOMATION_SKIP_BROWSER_INSTALL}${NC}"

# 检查并创建虚拟环境 (虚拟环境在项目根目录 .venv)
if [ ! -d ".venv" ]; then
    echo -e "${YELLOW}未找到虚拟环境，正在创建 .venv...${NC}"
    python3 -m venv .venv
fi

# 激活虚拟环境
source .venv/bin/activate

# 进入后端目录
cd "${PROJECT_ROOT}/backend" || exit

# 安装依赖
echo -e "${YELLOW}正在检查后端依赖...${NC}"
pip install -r requirements.txt -q

# 安装 Playwright Chromium（AUTOMATION_SKIP_BROWSER_INSTALL=1 可跳过）
if [ "${AUTOMATION_SKIP_BROWSER_INSTALL}" = "1" ]; then
    echo -e "${YELLOW}已设置 AUTOMATION_SKIP_BROWSER_INSTALL=1，跳过浏览器安装${NC}"
else
    echo -e "${YELLOW}检查 Playwright Chromium 安装状态...${NC}"
    python3 -m playwright install chromium >/dev/null 2>&1 || true
fi

# 启动后端服务 (后台运行)
echo -e "${GREEN}启动后端服务 (端口 8080)...${NC}"
# uvicorn 会继承当前 shell 的 stdout，把它放在后台运行
uvicorn app.main:app --reload --port 8080 &
BACKEND_PID=$!
# 等待1秒确保后端如果报错能打印出来
sleep 1

# 2. 启动前端
echo -e "\n${YELLOW}>>> 准备启动前端 (Vite/React)...${NC}"
cd "${PROJECT_ROOT}/frontend" || exit

# 清理前端端口占用
kill_port_process 5173

# 检查 node_modules
if [ ! -d "node_modules" ]; then
    echo -e "${YELLOW}未找到 node_modules，正在安装前端依赖...${NC}"
    npm install
fi

# 启动前端服务 (前台运行)
echo -e "${GREEN}启动前端服务...${NC}"
npm run dev &
FRONTEND_PID=$!

# 3. 扫尾工作 (捕获 Ctrl+C，一键关闭所有服务)
echo -e "\n${GREEN}===========================================${NC}"
echo -e "${GREEN}  服务已启动！${NC}"
echo -e "${GREEN}  前端页面: http://localhost:5173${NC}"
echo -e "${GREEN}  后端 API: http://localhost:8080${NC}"
echo -e "${GREEN}===========================================${NC}"
echo -e "${YELLOW}按 Ctrl+C 可以同时关闭前端和后端服务${NC}"

# 定义退出函数
cleanup() {
    echo -e "\n${RED}>>> 收到关闭信号，正在停止服务...${NC}"
    kill $BACKEND_PID 2>/dev/null
    kill $FRONTEND_PID 2>/dev/null
    echo -e "${GREEN}所有服务已停止。${NC}"
    exit 0
}

# 捕获 SIGINT (Ctrl+C) 和 SIGTERM
trap cleanup SIGINT SIGTERM

# 保持脚本运行，等待用户中断
wait $BACKEND_PID $FRONTEND_PID