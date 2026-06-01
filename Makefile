IMAGE_NAME ?= zyb_agent:latest
ARCHIVE ?= zyb_agent.tar.gz
REMOTE_HOST ?= root@139.196.90.36
REMOTE_DIR ?= /root/zybagent
SSH_PASSWORD ?=
APP_PORT ?= 35828

.PHONY: build up down logs save upload deploy clean-archive prune-local

build:
	docker build -t $(IMAGE_NAME) .

up:
	APP_PORT=$(APP_PORT) IMAGE_NAME=$(IMAGE_NAME) docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f zyb_agent

save:
	docker save $(IMAGE_NAME) | gzip -c > $(ARCHIVE)

upload:
	test -n "$(SSH_PASSWORD)"
	sshpass -p "$(SSH_PASSWORD)" scp -o StrictHostKeyChecking=accept-new $(ARCHIVE) $(REMOTE_HOST):$(REMOTE_DIR)/$(ARCHIVE)

deploy:
	test -n "$(SSH_PASSWORD)"
	sshpass -p "$(SSH_PASSWORD)" ssh -o StrictHostKeyChecking=accept-new $(REMOTE_HOST) "\
		set -e; \
		cd $(REMOTE_DIR); \
		gunzip -c $(ARCHIVE) | docker load; \
		docker compose up -d --force-recreate zyb_agent; \
		docker image prune -f; \
		docker ps --filter name=^/zyb_agent$$ --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'; \
		docker exec zyb_agent sh -lc 'which pandoc && pandoc --version | head -n 2' \
	"

clean-archive:
	rm -f $(ARCHIVE)

prune-local:
	docker image prune -f