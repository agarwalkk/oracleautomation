"""Pydantic request / response schemas for QCS Center."""
from __future__ import annotations

from pydantic import BaseModel, Field


class JobCreate(BaseModel):
    run_id: str = Field(..., description="Unique run identifier e.g. 'run_001'")
    instructions: str = Field(..., description="Plain-text recording instructions")
    auto_name: bool = Field(True, description="Skip interactive prompts; confirm as AI")


class JobStatus(BaseModel):
    id: str
    run_id: str
    instructions: str
    auto_name: bool
    status: str  # queued | claimed | running | done | failed | cancelled
    agent_name: str | None = None
    created_at: str
    claimed_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    recording_jsonl: str | None = None


class PollRequest(BaseModel):
    agent_name: str
    hostname: str = ""
    tags: str = ""  # comma-separated


class HeartbeatRequest(BaseModel):
    agent_name: str
    hostname: str = ""
    tags: str = ""
    current_job_id: str | None = None


class ResultPost(BaseModel):
    agent_name: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    recording_jsonl: str = ""


class AgentInfo(BaseModel):
    name: str
    last_heartbeat: str
    status: str
    current_job_id: str | None = None
    hostname: str | None = None
    tags: str | None = None
