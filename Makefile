CB_BIN ?= $(shell which cb)
CODE_ROOT ?= $(HOME)/code

daemon-image:
	@test -n "$(CB_BIN)" || (echo "cb binary not found; pass CB_BIN=/path/to/cb" && exit 1)
	mkdir -p bin && cp "$(CB_BIN)" bin/cb
	docker build -f container/daemon/Dockerfile -t agent-orchestration-daemon .

daemon-run:
	docker rm -f agent-orchestration-daemon 2>/dev/null || true
	docker run -d --name agent-orchestration-daemon --restart=always \
	  -v /var/run/docker.sock:/var/run/docker.sock \
	  -v $(HOME)/.agent-orchestration:/root/.agent-orchestration \
	  -v $(HOME)/.claude:/root/.claude:ro \
	  -v $(CODE_ROOT):$(CODE_ROOT) \
	  -e KENTRA_BOT_GH_TOKEN -e ORCHESTRATION_DAEMON_TOKEN \
	  -p 8765:8765 -p 42000-42050:42000-42050 \
	  agent-orchestration-daemon
	@echo "daemon: http://localhost:8765"

daemon-logs:
	docker logs -f agent-orchestration-daemon
