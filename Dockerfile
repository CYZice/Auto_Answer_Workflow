# 阶段 1: 构建前端
FROM node:20-slim AS frontend-builder

WORKDIR /build

# 复制前端代码
COPY frontend/package*.json ./
COPY frontend/ ./

# 安装依赖并构建
RUN npm install && npm run build

# 阶段 2: 运行后端
FROM python:3.11-slim

WORKDIR /app

# 安装 Word 渲染、PDF 文本坐标和页面截图依赖
RUN sed -i \
    -e 's|deb.debian.org/debian-security|mirrors.aliyun.com/debian-security|g' \
    -e 's|deb.debian.org/debian|mirrors.aliyun.com/debian|g' \
    /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
    curl \
    pandoc \
    libreoffice-writer \
    poppler-utils \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# 复制后端 requirements
COPY backend/requirements.txt .

# 安装 Python 依赖
RUN pip install --no-cache-dir --upgrade pip setuptools "wheel>=0.46.2"
RUN pip install --no-cache-dir -r requirements.txt

# 安装 gunicorn + uvicorn workers
RUN pip install --no-cache-dir gunicorn

# 从阶段 1 复制前端构建产物
COPY --from=frontend-builder /build/dist ./frontend/dist/

# 复制后端代码
COPY backend/ .

# 创建 data 目录
RUN mkdir -p /app/data

EXPOSE 38080

CMD ["gunicorn", "--bind", "0.0.0.0:38080", "-w", "1", "-k", "uvicorn.workers.UvicornWorker", "app.main:app"]
