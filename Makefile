# zyb_agent 部署 Makefile
# 用法: make build | save | upload | upload-data | deploy | server-help

IMAGE ?= zyb_agent:latest
SERVER ?= root@139.196.90.36
REMOTE_DIR ?= /root/zybagent
SERVER_PASS ?= f+9WS9t5Bx9&9Xj

.PHONY: build save upload upload-data deploy server-help

# 本地构建镜像
build:
	docker build -t $(IMAGE) .

# 打包镜像为 tar.gz
save: build
	docker save $(IMAGE) | gzip -c > zyb_agent.tar.gz
	@echo "已生成 zyb_agent.tar.gz"

# 上传到服务器（使用 sshpass 自动输入密码）
upload: save
	sshpass -p '$(SERVER_PASS)' scp zyb_agent.tar.gz docker-compose.yml $(SERVER):$(REMOTE_DIR)/
	@echo "上传完成，在服务器执行: make server-load"

# 上传数据目录到服务器（单独执行）
upload-data:
	sshpass -p '$(SERVER_PASS)' rsync -avz --progress data/ $(SERVER):$(REMOTE_DIR)/data/
	@echo "数据上传完成"

# 在服务器上加载镜像
server-load:
	@if [ ! -f $(REMOTE_DIR)/zyb_agent.tar.gz ]; then echo "需要先上传镜像到 $(REMOTE_DIR)"; exit 1; fi
	sshpass -p '$(SERVER_PASS)' ssh $(SERVER) "cd $(REMOTE_DIR) && gunzip -c zyb_agent.tar.gz | docker load"
	@echo "加载完成，执行: docker compose up -d"

# 服务器上一键部署
deploy:
	@echo "=== 服务器部署 ==="
	@echo "1. 本地执行: make upload"
	@echo "2. 服务器执行:"
	@echo "   cd $(REMOTE_DIR)"
	@echo "   gunzip -c zyb_agent.tar.gz | docker load"
	@echo "   docker compose up -d"

# 服务器管理命令（在服务器上执行）
server-help:
	@echo "=== 服务器部署命令 ==="
	@echo ""
	@echo "首次部署:"
	@echo "  mkdir -p $(REMOTE_DIR)"
	@echo "  # 上传镜像和 docker-compose.yml:"
	@echo "  make upload"
	@echo "  # 上传数据（可选，首次必须）:"
	@echo "  make upload-data"
	@echo "  # 服务器执行:"
	@echo "  cd $(REMOTE_DIR)"
	@echo "  gunzip -c zyb_agent.tar.gz | docker load"
	@echo "  docker compose up -d"
	@echo ""
	@echo "日常更新:"
	@echo "  cd $(REMOTE_DIR)"
	@echo "  git pull"
	@echo "  make build && make save"
	@echo "  make upload"
	@echo "  docker compose up -d --force-recreate"
	@echo ""
	@echo "查看日志:"
	@echo "  cd $(REMOTE_DIR) && docker compose logs -f"
	@echo ""
	@echo "同步数据:"
	@echo "  make upload-data"

# 清理
clean:
	rm -f zyb_agent.tar.gz
	docker rmi $(IMAGE) || true
