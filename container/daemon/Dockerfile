# agent-orchestration daemon (design §6). Build FROM THE HOST (the image needs
# the real docker socket at runtime; building in-box is pointless).
#   make daemon-image      # no CB_BIN needed — cb is built from source below
FROM golang:1.26-bookworm AS lifecycle-build
RUN go install github.com/kentra-io/spec-lifecycle/cmd/lifecycle@4d1f002755ac

# cb (claudebox CLI) — kentra-io/claudebox ships no prebuilt linux/arm64 binary,
# so build it here from a PINNED public ref for the image's own arch. This
# removes the host-binary copy that let a macOS `claudebox` get baked into the
# linux image (Mach-O → "Exec format error" at launch, issue #21). Bump the ref
# deliberately, like the lifecycle pin above (ADR-0001 spirit); keep it in sync
# with CLAUDEBOX_REF in .github/workflows/publish-daemon.yml.
FROM golang:1.26-bookworm AS cb-build
ARG CLAUDEBOX_REF=92d7855f654803dd7cef278d7607c65ae77e0a11
RUN git clone --quiet https://github.com/kentra-io/claudebox /src \
    && git -C /src checkout --quiet "$CLAUDEBOX_REF" \
    && cd /src && CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -o /cb .

FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# The daemon runs as root and drives `git worktree add` against host-owned repos
# bind-mounted from the host (a different uid) — git refuses these as "dubious
# ownership" unless they are trusted. The daemon only ever touches repos the
# operator mounted, so trust all of them system-wide.
RUN git config --system --add safe.directory '*'
# docker CLI (static client only — the daemon talks to the mounted host socket)
RUN curl -fsSL https://download.docker.com/linux/static/stable/aarch64/docker-27.5.1.tgz \
    | tar -xz --strip-components=1 -C /usr/local/bin docker/docker
# gh CLI
RUN curl -fsSL https://github.com/cli/cli/releases/download/v2.86.0/gh_2.86.0_linux_arm64.tar.gz \
    | tar -xz --strip-components=2 -C /usr/local/bin gh_2.86.0_linux_arm64/bin/gh
COPY --from=lifecycle-build /go/bin/lifecycle /usr/local/bin/lifecycle
# claude CLI (native installer, standalone binary): cb's auth pre-flight shells
# out to `claude auth status --json` before provisioning a box, so the daemon
# image must carry it. Installs to /root/.local/bin (daemon runs as root).
RUN curl -fsSL https://claude.ai/install.sh | bash
# cb (claudebox CLI) — built for this image's arch in the cb-build stage above.
COPY --from=cb-build /cb /usr/local/bin/cb

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY orchestration ./orchestration
COPY workflows ./workflows
COPY personas ./personas
RUN pip install --no-cache-dir uv==0.11.27 \
    && UV_PROJECT_ENVIRONMENT=/opt/venv uv sync --frozen --no-dev
ENV PATH="/opt/venv/bin:/root/.local/bin:${PATH}" \
    CONDUCTOR_WEB_HOST=0.0.0.0 \
    ORCHESTRATION_WEB_PORT_RANGE=42000-42050
EXPOSE 8765
ENTRYPOINT ["python", "-m", "orchestration.daemon"]
