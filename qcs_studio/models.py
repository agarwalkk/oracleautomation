from __future__ import annotations

from pydantic import BaseModel, Field


class ScanRequest(BaseModel):
    pid: int | None = None
    contains: str | None = None


class ScanSaveRequest(BaseModel):
    scan_id: str
    container_ref: str | None = None
    title: str | None = None
    metadata: dict = Field(default_factory=dict)
    # Optional user-edited curated display tree (e.g. after drag-to-add).
    # When provided it overrides the scan's auto-built tree on save.
    display_tree: list[dict] | None = None


class ContainerTreeUpdateRequest(BaseModel):
    title: str | None = None
    metadata: dict | None = None
    tree: list[dict]


class DisplayTreeUpdateRequest(BaseModel):
    """Update only the curated display tree of a saved container."""
    display_tree: list[dict]
    title: str | None = None


class ElementUpdateRequest(BaseModel):
    updates: dict
