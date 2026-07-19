"""The daemon app: control plane + index (design §3.1, §5.2).

POST /resume is deliberately 501 in observability-core: resume stays
CLI-direct until the github-mirror change (design §10, resolved).
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from orchestration.daemon.ports import PortAllocator, parse_range
from orchestration.daemon.supervise import Supervisor
from orchestration.launch.change import launch as _launch_fn
from orchestration.obs import registry
from orchestration.obs.status import collect, derive_state

POLL_INTERVAL_S = 2.0


def _folded_runs() -> list[dict[str, Any]]:
    runs = []
    for entry in registry.load_entries():
        try:
            derived = derive_state(entry, collect(entry))
        except OSError:
            derived = {"state": "unknown (fold error)", "stalled": False, "classified": None}
        runs.append({**entry, "derived": derived})
    return runs


def create_app(supervisor: Supervisor, token: str | None = None) -> FastAPI:
    lo, hi = parse_range(os.environ.get("ORCHESTRATION_WEB_PORT_RANGE", "42000-42050"))
    allocator = PortAllocator(lo, hi)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        async def loop() -> None:
            while True:
                supervisor.poll_once()
                supervisor.reconcile()
                await asyncio.sleep(POLL_INTERVAL_S)

        task = asyncio.create_task(loop())
        yield
        task.cancel()

    app = FastAPI(title="agent-orchestration daemon", lifespan=lifespan)

    def _check_token(request: Request) -> None:
        if token and request.headers.get("Authorization") != f"Bearer {token}":
            raise HTTPException(status_code=401, detail="bad or missing bearer token")

    @app.get("/runs")
    async def runs() -> dict[str, Any]:
        return {"runs": _folded_runs()}

    @app.post("/launch")
    async def launch_run(request: Request) -> dict[str, Any]:
        _check_token(request)
        payload = await request.json()
        if not payload.get("repo") or not payload.get("change_id"):
            raise HTTPException(status_code=422, detail="repo and change_id are required")
        conductor_cfg = dict(payload.get("conductor") or {})
        conductor_cfg["web"] = True
        conductor_cfg["web_port"] = allocator.allocate()
        payload["conductor"] = conductor_cfg
        holder: dict[str, Any] = {}
        report = await run_in_threadpool(_launch_fn, payload, holder)
        if holder.get("proc") is not None:
            supervisor.adopt(
                registry.repo_slug(payload["repo"]), payload["change_id"], holder["proc"]
            )
        return {"report": report}

    @app.post("/resume")
    async def resume() -> None:
        raise HTTPException(
            status_code=501,
            detail="resume stays CLI-direct in observability-core (see design §10)",
        )

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        rows = []
        for run in _folded_runs():
            last = run["incarnations"][-1] if run["incarnations"] else {}
            dash = last.get("dashboard_url")
            issue = run.get("issue")
            issue_link = (
                f'<a href="https://github.com/kentra-io/{run["repo_slug"]}/issues/{issue}">#{issue}</a>'
                if issue
                else ""
            )
            rows.append(
                "<tr>"
                f"<td>{html.escape(run['repo_slug'])}</td>"
                f"<td>{html.escape(run['change_id'])}</td>"
                f"<td>{html.escape(run['derived']['state'])}"
                f"{' ⚠ stalled?' if run['derived']['stalled'] else ''}</td>"
                f"<td>{f'<a href={chr(34)}{dash}{chr(34)}>dashboard</a>' if dash else ''}</td>"
                f"<td>{issue_link}</td>"
                "</tr>"
            )
        return (
            "<html><head><title>agent-orchestration</title></head><body>"
            "<h1>agent-orchestration runs</h1>"
            "<table border=1 cellpadding=6><tr><th>project</th><th>change</th>"
            "<th>state</th><th>live</th><th>issue</th></tr>"
            + "".join(rows)
            + "</table></body></html>"
        )

    return app
