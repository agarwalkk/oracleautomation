from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from qcs_repo import store as repo_store
from qcs_studio.models import (
    ContainerTreeUpdateRequest,
    DisplayTreeUpdateRequest,
    ElementUpdateRequest,
    RecalculateTreeRequest,
    ScanRequest,
    ScanSaveRequest,
)
from qcs_studio.service import StudioService


router = APIRouter()
_service = StudioService()


@router.get("/windows")
def list_windows() -> dict:
    return {"items": _service.list_windows()}


@router.post("/scan")
def run_scan(request: ScanRequest) -> dict:
    """Phase 1: capture raw DOM + screenshot from a live Oracle window.

    Returns the scan immediately with empty tree. The client should then call
    POST /scan/recalculate to compute the AI snapshot tree (Phase 2)."""
    try:
        bundle = _service.run_scan(pid=request.pid, contains=request.contains)
    except Exception as exc:  # pragma: no cover - pass-through for UI diagnostics
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "scan_id": bundle.scan_id,
        "title": bundle.title,
        "container_ref": bundle.container_ref,
        "raw_dom": bundle.raw_dom,
        "snapshot_text": bundle.snapshot_text,
        "tree": bundle.tree,
        "full_elements": bundle.full_elements,
        "screenshot_origin": bundle.screenshot_origin,
        "capture_mode": bundle.capture_mode,
        "screenshot_path": str(bundle.screenshot_path),
        "created_at": bundle.created_at,
    }


@router.post("/scan/recalculate")
def recalculate_tree(request: RecalculateTreeRequest) -> dict:
    """Phase 2: rebuild AI snapshot + tree + full_elements from cached raw DOM.

    Does NOT require a live Oracle window — works from the persisted raw DOM
    captured in Phase 1. Can be called repeatedly (e.g. after prompt tuning).
    """
    try:
        bundle = _service.compute_tree(request.scan_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "scan_id": bundle.scan_id,
        "title": bundle.title,
        "container_ref": bundle.container_ref,
        "snapshot_text": bundle.snapshot_text,
        "tree": bundle.tree,
        "full_elements": bundle.full_elements,
        "screenshot_origin": bundle.screenshot_origin,
        "capture_mode": bundle.capture_mode,
        "screenshot_path": str(bundle.screenshot_path),
        "created_at": bundle.created_at,
    }


@router.post("/scan/save")
def save_scan(request: ScanSaveRequest) -> dict:
    try:
        saved = _service.save_scan(
            request.scan_id,
            container_ref=request.container_ref,
            title=request.title,
            metadata=request.metadata,
            display_tree=request.display_tree,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return saved


@router.get("/scans/{scan_id}/screenshot")
def get_scan_screenshot(scan_id: str):
    bundle = _service.get_cached_scan(scan_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail=f"Unknown scan_id {scan_id!r}")
    if not bundle.screenshot_path.exists():
        raise HTTPException(status_code=404, detail="Scan screenshot not found")
    return FileResponse(bundle.screenshot_path)


@router.get("/containers")
def list_containers() -> dict:
    # Include both persisted containers and in-memory draft scans. Drafts
    # appear at the top so the user can see their unsaved work immediately.
    persisted = [item for item in repo_store.list_containers() if str(item.get("status") or "") != "draft"]
    # Merge drafts as lightweight entries.
    draft_entries = []
    for d in _service.list_drafts():
        draft_entries.append({
            "container_ref": d["scan_id"],  # draft keyed by scan_id
            "title": d["title"],
            "screenshot": None,
            "status": "draft",
            "scan_id": d["scan_id"],
            "has_tree": d["has_tree"],
            "created_at": d["created_at"],
        })
    # Drafts first, then persisted
    items = draft_entries + persisted
    return {"items": items}


@router.get("/drafts")
def list_drafts() -> dict:
    """List all in-memory draft scans (survive page refresh, not persisted to repo)."""
    return {"items": _service.list_drafts()}


@router.get("/drafts/{scan_id}")
def get_draft(scan_id: str) -> dict:
    """Load a cached draft scan bundle (includes screenshot path, tree, etc.)."""
    bundle = _service.load_draft(scan_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail=f"Unknown draft {scan_id!r}")
    return {
        "scan_id": bundle.scan_id,
        "title": bundle.title,
        "container_ref": bundle.container_ref,
        "snapshot_text": bundle.snapshot_text,
        "tree": bundle.tree,
        "full_elements": bundle.full_elements,
        "screenshot_origin": bundle.screenshot_origin,
        "capture_mode": bundle.capture_mode,
        "screenshot_path": str(bundle.screenshot_path),
        "created_at": bundle.created_at,
    }


@router.delete("/drafts/{scan_id}")
def delete_draft(scan_id: str) -> dict:
    """Delete an in-memory draft scan and its temporary screenshot."""
    if not _service.delete_draft(scan_id):
        raise HTTPException(status_code=404, detail=f"Unknown draft {scan_id!r}")
    return {"ok": True}


@router.get("/containers/{container_ref}")
def get_container(container_ref: str) -> dict:
    container = repo_store.load_container(container_ref)
    if container is None:
        raise HTTPException(status_code=404, detail=f"Unknown container_ref {container_ref!r}")
    # Attach the full hoverable element overlay so a reloaded container supports
    # the same hover-inspect / drag-to-tree workflow as a live scan.
    container["full_elements"] = _service.full_elements_for_container(container)
    return container


@router.put("/containers/{container_ref}")
def update_container_tree(container_ref: str, request: ContainerTreeUpdateRequest) -> dict:
    existing = repo_store.load_container(container_ref)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Unknown container_ref {container_ref!r}")
    return repo_store.save_container_tree(
        container_ref,
        request.tree,
        title=request.title or existing.get("title") or container_ref,
        surface=existing.get("surface") or "java_forms",
        fingerprint=existing.get("fingerprint"),
        screenshot=existing.get("screenshot"),
        raw_dom_path=existing.get("raw_dom_path"),
        source=existing.get("source") or "manual",
        status=existing.get("status") or "active",
        metadata=request.metadata or existing.get("metadata") or {},
    )


@router.put("/containers/{container_ref}/display-tree")
def update_container_display_tree(
    container_ref: str, request: DisplayTreeUpdateRequest
) -> dict:
    """Persist only the curated display tree (e.g. after drag-to-add) for a
    saved container, leaving the underlying element catalog intact."""
    existing = repo_store.load_container(container_ref)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Unknown container_ref {container_ref!r}")
    metadata = dict(existing.get("metadata") or {})
    metadata["display_tree"] = request.display_tree
    return repo_store.save_container_tree(
        container_ref,
        existing.get("tree") or [],
        title=request.title or existing.get("title") or container_ref,
        surface=existing.get("surface") or "java_forms",
        fingerprint=existing.get("fingerprint"),
        screenshot=existing.get("screenshot"),
        raw_dom_path=existing.get("raw_dom_path"),
        source=existing.get("source") or "manual",
        status=existing.get("status") or "active",
        metadata=metadata,
    )


@router.patch("/containers/{container_ref}/elements/{element_ref}")
def patch_element(container_ref: str, element_ref: str, request: ElementUpdateRequest) -> dict:
    try:
        return repo_store.upsert_container_element(container_ref, element_ref, request.updates)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/containers/{container_ref}/elements/{element_ref}")
def delete_element(container_ref: str, element_ref: str) -> dict:
    try:
        return repo_store.delete_container_element(container_ref, element_ref)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
