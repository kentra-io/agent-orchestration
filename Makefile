CODE_ROOT ?= $(HOME)/code

# cb is built from source inside the Dockerfile (cb-build stage, pinned
# CLAUDEBOX_REF) — no host binary, so no CB_BIN and no macOS-arch footgun (#21).
daemon-image:
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
