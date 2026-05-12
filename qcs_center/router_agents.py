"""Agent poll, heartbeat, and result routes — /api/v1/agents."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from qcs_center import db
from qcs_center.models import AgentInfo, HeartbeatRequest, PollRequest, ResultPost

router = APIRouter(tags=["agents"])

_POLL_TIMEOUT_S = 29.0   # max seconds to wait for a job before returning null
_POLL_INTERVAL_S = 0.5   # how often to check for new jobs while waiting


@router.post("/agents/poll", response_model=dict)
async def poll_for_job(body: PollRequest) -> dict:
    """Long-poll: agent calls this and waits up to 29 s for a queued job.

    Returns ``{"job": <dict>}`` when work is available, or ``{"job": null}``
    on timeout so the agent can call again immediately.
    """
    await db.agent_heartbeat(body.agent_name, body.hostname, body.tags)

    elapsed = 0.0
    while elapsed < _POLL_TIMEOUT_S:
        job = await db.job_dequeue()
        if job is not None:
            await db.agent_set_busy(body.agent_name, job["id"])
            return {"job": dict(job)}
        await asyncio.sleep(_POLL_INTERVAL_S)
        elapsed += _POLL_INTERVAL_S

    return {"job": None}


@router.post("/agents/heartbeat")
async def heartbeat(body: HeartbeatRequest) -> dict:
    """Agent keepalive — refreshes last_heartbeat timestamp."""
    await db.agent_heartbeat(body.agent_name, body.hostname, body.tags)
    return {"ok": True}


@router.post("/agents/result/{job_id}")
async def post_result(job_id: str, body: ResultPost) -> dict:
    """Agent posts the outcome of a completed recording job."""
    row = await db.job_get(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    await db.job_finish(
        job_id,
        body.exit_code,
        body.stdout,
        body.stderr,
        body.recording_jsonl,
    )
    await db.agent_set_idle(body.agent_name)
    final_status = "done" if body.exit_code == 0 else "failed"
    return {"ok": True, "status": final_status}


@router.get("/agents", response_model=list[AgentInfo])
async def list_agents() -> list[AgentInfo]:
    """List all registered agents with their current status."""
    rows = await db.agent_list()
    return [AgentInfo(**r) for r in rows]
