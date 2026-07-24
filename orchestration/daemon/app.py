"""The daemon app: control plane + index (design §3.1, §5.2).

POST /resume is a real, token-gated sibling of /launch (design §8):
re-derives remaining milestones from the current plan, resumes in place or
starts a fresh run over the remaining list, allocates a dashboard port,
adopts the child, and appends a new incarnation.
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

from orchestration.daemon import github_mirror
from orchestration.daemon.ports import PortAllocator, parse_range
from orchestration.daemon.resume import ResumeError
from orchestration.daemon.resume import resume as _resume_fn
from orchestration.daemon.supervise import Supervisor
from orchestration.launch.change import launch as _launch_fn
from orchestration.obs import registry
from orchestration.obs.status import collect, derive_state

POLL_INTERVAL_S = 2.0


def _mirror_started_safe(slug: str, change_id: str, resumed: bool) -> None:
    """Post the run-started/-resumed comment, never letting a mirror error
    surface into the request handler (every GitHub write is best effort)."""
    try:
        entry = registry.load_entry(slug, change_id)
        if entry is not None:
            github_mirror.mirror_started(entry, resumed=resumed)
    except Exception:  # noqa: BLE001 - a mirror failure must never fail the launch/resume
        pass


def _mirror_terminal_events(events: list[dict[str, Any]]) -> None:
    """Hand supervision terminal events to the mirror, never letting a mirror
    error kill the poll loop (design D5: best-effort, independent)."""
    for event in events:
        try:
            entry = registry.load_entry(event["slug"], event["change_id"])
            if entry is not None:
                github_mirror.mirror_terminal(entry, event)
        except Exception:  # noqa: BLE001 - never let a mirror error kill the loop
            pass


def _poll_and_mirror(supervisor: Supervisor) -> None:
    events = supervisor.poll_once() + supervisor.reconcile()
    _mirror_terminal_events(events)


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
                await run_in_threadpool(_poll_and_mirror, supervisor)
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
        slug = registry.repo_slug(payload["repo"])
        if holder.get("proc") is not None:
            supervisor.adopt(slug, payload["change_id"], holder["proc"])
            await run_in_threadpool(_mirror_started_safe, slug, payload["change_id"], False)
        return {"report": report}

    @app.post("/resume")
    async def resume_run(request: Request) -> dict[str, Any]:
        _check_token(request)
        payload = await request.json()
        repo, change_id = payload.get("repo"), payload.get("change_id")
        if not repo or not change_id:
            raise HTTPException(status_code=422, detail="repo and change_id are required")
        slug = registry.repo_slug(repo)
        entry = registry.load_entry(slug, change_id)
        if entry is None:
            raise HTTPException(
                status_code=404, detail=f"nothing to resume for {slug}--{change_id}"
            )
        derived = derive_state(entry, collect(entry))
        if derived["state"] == "running":
            raise HTTPException(status_code=409, detail="run is still alive — nothing to resume")
        web_port = allocator.allocate()
        holder: dict[str, Any] = {}
        try:
            report = await run_in_threadpool(
                _resume_fn, entry, web_port=web_port, proc_holder=holder
            )
        except ResumeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if holder.get("proc") is not None:
            supervisor.adopt(slug, change_id, holder["proc"])
            await run_in_threadpool(_mirror_started_safe, slug, change_id, True)
        return {"report": report}

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        rows = []
        for run in _folded_runs():
            last = run["incarnations"][-1] if run["incarnations"] else {}
            # The dashboard is served by the run's own conductor process, so
            # the link is only live while that process is (state "running");
            # for finished/dead runs the registry URL is historical — and the
            # port may since have been re-allocated to a different run.
            dash = last.get("dashboard_url") if run["derived"]["state"] == "running" else None
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
