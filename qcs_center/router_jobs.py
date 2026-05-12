"""Job submission and status routes — /api/v1/jobs."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from qcs_center import db
from qcs_center.models import JobCreate, JobStatus

router = APIRouter(tags=["jobs"])


def _to_status(row: dict) -> JobStatus:
    return JobStatus(
        id=row["id"],
        run_id=row["run_id"],
        instructions=row["instructions"],
        auto_name=bool(row["auto_name"]),
        status=row["status"],
        agent_name=row.get("agent_name"),
        created_at=row["created_at"],
        claimed_at=row.get("claimed_at"),
        finished_at=row.get("finished_at"),
        exit_code=row.get("exit_code"),
        stdout=row.get("stdout"),
        stderr=row.get("stderr"),
        recording_jsonl=row.get("recording_jsonl"),
    )


@router.post("/jobs", response_model=JobStatus, status_code=status.HTTP_201_CREATED)
async def create_job(body: JobCreate) -> JobStatus:
    """Submit a new recording job."""
    row = await db.job_create(body.run_id, body.instructions, body.auto_name)
    return _to_status(row)


@router.get("/jobs", response_model=list[JobStatus])
async def list_jobs(
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=1000),
) -> list[JobStatus]:
    """List jobs, optionally filtered by status."""
    rows = await db.job_list(status=status_filter, limit=limit)
    return [_to_status(r) for r in rows]


@router.get("/jobs/{job_id}", response_model=JobStatus)
async def get_job(job_id: str) -> JobStatus:
    """Get a single job by ID (includes result once finished)."""
    row = await db.job_get(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _to_status(row)
