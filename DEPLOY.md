# 部署文档

## 服务器信息

| 项目 | 值 |
|------|-----|
| 公网 IP | 139.196.90.36 |
| 私有 IP | 172.24.49.66 |
| SSH 端口 | 22 |
| 用户 | root |
| 密码 | `f+9WS9t5Bx9&9Xj` |

## 服务地址

- **端口**: 35828
- **访问**: http://139.196.90.36:35828

## 服务器目录结构

```
/root/zybagent/          # 项目根目录（只需这两个文件/目录）
├── docker-compose.yml    # 容器编排配置
└── data/               # 数据目录（SQLite 数据库等）
```

> 注意：所有代码已打包在 Docker 镜像中，无需在服务器存放代码。

## 部署方式

### 方式一：tar 包部署（推荐，无需镜像仓库）

#### 1. 本地构建并打包

```bash
make build
make save
```

#### 2. 上传到服务器

```bash
make upload
```

> 注意：首次部署需要先在服务器上创建目录：
> ```bash
> ssh root@139.196.90.36 "mkdir -p /root/zybagent"
> ```

#### 3. 服务器上加载镜像

```bash
ssh root@139.196.90.36
```

密码：`f+9WS9t5Bx9&9Xj`

```bash
cd /root/zybagent
gunzip -c zyb_agent.tar.gz | docker load
```

#### 4. 启动服务

```bash
docker compose up -d
```

#### 5. 验证

```bash
curl http://139.196.90.36:35828
```

---

### 方式二：镜像仓库

#### 1. 构建并推送

```bash
make push REGISTRY=registry.cn-hangzhou.aliyuncs.com/你的命名空间
```

#### 2. 服务器拉取启动

```bash
ssh root@139.196.90.36
cd /root/zybagent
docker compose pull
docker compose up -d
```

---

## 数据同步

### 同步 data 目录（重要）

项目依赖 `data/` 目录，包含：
- SQLite 数据库文件

```bash
# 在有数据的机器上执行（将 data 目录同步到服务器）
make upload-data
```

---

## 服务管理

```bash
# 查看日志
cd /root/zybagent && docker compose logs -f

# 查看状态
docker compose ps

# 重启服务
cd /root/zybagent && docker compose restart

# 停止服务
cd /root/zybagent && docker compose down

# 更新部署（重新打包上传）
# 1. 本地重新打包
make build && make save
# 2. 上传
make upload
# 3. 服务器加载并重启
ssh root@139.196.90.36 "cd /root/zybagent && gunzip -c zyb_agent.tar.gz | docker load && docker compose up -d --force-recreate"
```

---

## 环境变量配置

在 `docker-compose.yml` 中已配置以下环境变量：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LLM_API_KEY` | (必填) | OpenAI API Key |
| `LLM_BASE_URL` | https://api.openai.com/v1 | API 基础 URL |
| `LLM_MODEL_NAME` | gpt-4o-mini | 模型名称 |

---

## 服务器要求

- Docker 已安装
- 端口 35828 已开放（防火墙）
- data 目录已同步（包含 SQLite 数据库）

---

## 快速部署脚本

在服务器上创建 `deploy.sh` 方便日常部署：

```bash
#!/bin/bash
# /root/zybagent/deploy.sh

set -e

cd /root/zybagent

echo "=== 停止旧容器 ==="
docker compose down

echo "=== 加载新镜像 ==="
gunzip -c zyb_agent.tar.gz | docker load

echo "=== 启动服务 ==="
docker compose up -d

echo "=== 检查状态 ==="
docker compose ps
curl -s http://localhost:35828 > /dev/null && echo "服务正常" || echo "服务异常"
```
