"""
qcs_repo.store — Load and save the Object Repository.

Layout
------
repo/
    repo.db                     SQLite form and element catalog
    index.yaml                  legacy fingerprint → form_id, flow_name → flow_id
    forms/<form_id>.yaml        legacy form metadata fallback
    elements/<form_id>.yaml     legacy element catalog fallback
  flows/<flow_id>.yaml        reusable step sequences

YAML remains for flows and old data fallback. Forms and elements are stored in SQLite.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

import config
from qcs_repo import identity as repo_identity

if TYPE_CHECKING:
    from qcs_repo.schema import RepoEntry


# ── Helpers ─────────────────────────────────────────────────────────────────

def _yaml_load(path: Path) -> Any:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _yaml_save(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
        shutil.move(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _repo_db_path(repo_dir: Path = config.REPO_DIR) -> Path:
    repo_dir = Path(repo_dir)
    if repo_dir == config.REPO_DIR:
        return config.REPO_DB_PATH
    return repo_dir / "repo.db"


def repo_db_path(repo_dir: Path = config.REPO_DIR) -> Path:
    """Return the SQLite catalog path for a repository directory."""
    return _repo_db_path(repo_dir)


def _db_connect(repo_dir: Path = config.REPO_DIR) -> sqlite3.Connection:
    db_path = _repo_db_path(repo_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS containers (
            container_ref TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            surface TEXT NOT NULL DEFAULT '',
            fingerprint TEXT,
            screenshot TEXT,
            raw_dom_path TEXT,
            tree_path TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            source TEXT NOT NULL DEFAULT 'recording',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_containers_surface ON containers(surface)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_containers_status ON containers(status)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS container_elements (
            container_ref TEXT NOT NULL,
            element_ref TEXT NOT NULL,
            parent_ref TEXT,
            raw_node_id TEXT,
            friendly_name TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL DEFAULT '',
            included INTEGER NOT NULL DEFAULT 1,
            x INTEGER NOT NULL DEFAULT 0,
            y INTEGER NOT NULL DEFAULT 0,
            width INTEGER NOT NULL DEFAULT 0,
            height INTEGER NOT NULL DEFAULT 0,
            screen_x INTEGER NOT NULL DEFAULT 0,
            screen_y INTEGER NOT NULL DEFAULT 0,
            screen_width INTEGER NOT NULL DEFAULT 0,
            screen_height INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'recording',
            status TEXT NOT NULL DEFAULT 'active',
            confidence REAL NOT NULL DEFAULT 1.0,
            descriptor_json TEXT NOT NULL DEFAULT '{}',
            locator_candidates_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            data_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (container_ref, element_ref),
            FOREIGN KEY (container_ref) REFERENCES containers(container_ref) ON DELETE CASCADE
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_container_elements_parent ON container_elements(container_ref, parent_ref)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_container_elements_role ON container_elements(container_ref, role)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS forms (
            id TEXT PRIMARY KEY,
            surface TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            fingerprint TEXT,
            screenshot TEXT,
            first_seen TEXT,
            updated_at TEXT,
            data_json TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_forms_fingerprint ON forms(fingerprint)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS elements (
            form_id TEXT NOT NULL,
            elementid TEXT NOT NULL,
            friendly_name TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            xpath TEXT NOT NULL DEFAULT '',
            x INTEGER NOT NULL DEFAULT 0,
            y INTEGER NOT NULL DEFAULT 0,
            width INTEGER NOT NULL DEFAULT 0,
            height INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1,
            confirmed_by TEXT NOT NULL DEFAULT '',
            first_seen TEXT,
            last_seen TEXT,
            data_json TEXT NOT NULL,
            PRIMARY KEY (form_id, elementid),
            FOREIGN KEY (form_id) REFERENCES forms(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_elements_form_enabled ON elements(form_id, enabled)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_elements_form_name ON elements(form_id, name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_elements_form_friendly ON elements(form_id, friendly_name)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS repo_entries (
            form_ref TEXT NOT NULL,
            element_ref TEXT NOT NULL,
            qualified_ref TEXT NOT NULL DEFAULT '',
            friendly_name TEXT NOT NULL DEFAULT '',
            surface TEXT NOT NULL DEFAULT '',
            object_type TEXT NOT NULL DEFAULT '',
            descriptor_json TEXT NOT NULL DEFAULT '{}',
            fallback_descriptors_json TEXT NOT NULL DEFAULT '[]',
            source TEXT NOT NULL DEFAULT 'recording',
            confidence REAL NOT NULL DEFAULT 1.0,
            status TEXT NOT NULL DEFAULT 'active',
            last_validated_run TEXT,
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (form_ref, element_ref)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_repo_entries_form ON repo_entries(form_ref)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_repo_entries_qualified ON repo_entries(qualified_ref)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_repo_entries_status ON repo_entries(status)")
    conn.commit()


def _json_dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def _json_load(raw: str) -> Any:
    return json.loads(raw) if raw else None


def _artifact_dir(container_ref: str, repo_dir: Path = config.REPO_DIR) -> Path:
    safe_ref = repo_identity.normalize_ref(container_ref)
    return Path(repo_dir) / "containers" / safe_ref


def _element_ref_from_tree_node(node: dict, used: set[str]) -> str:
    candidate = (
        node.get("element_ref")
        or node.get("semantic_ref")
        or node.get("friendly_name")
        or node.get("name")
        or node.get("elementid")
        or "element"
    )
    base = repo_identity.normalize_ref(str(candidate) or "element")
    if base not in used:
        used.add(base)
        return base
    idx = 2
    while f"{base}_{idx}" in used:
        idx += 1
    ref = f"{base}_{idx}"
    used.add(ref)
    return ref


def _descriptor_from_node(node: dict) -> dict[str, Any]:
    java = node.get("java") or {}
    bounds = node.get("bounds") or {}
    descriptor: dict[str, Any] = {}
    path = node.get("path") or node.get("xpath") or java.get("path")
    if path:
        descriptor["locatorPath"] = str(path)
    name = node.get("name") or java.get("name")
    if name:
        descriptor["locatorName"] = str(name)
    acc_name = java.get("accessibleName")
    if acc_name:
        descriptor["locatorAccessibleName"] = str(acc_name)
    text = node.get("text") or java.get("displayName")
    if text:
        descriptor["locatorText"] = str(text)
    if bounds:
        bx = int(bounds.get("x") or 0)
        by = int(bounds.get("y") or 0)
        bw = int(bounds.get("width") or 0)
        bh = int(bounds.get("height") or 0)
        descriptor["locatorBounds"] = f"{bx},{by},{bw},{bh}"
    return descriptor


def _normalize_tree_elements(tree_elements: list[dict]) -> list[dict]:
    now = _now_iso()
    used_refs: set[str] = set()
    normalized: list[dict] = []
    for node in tree_elements:
        element_ref = _element_ref_from_tree_node(node, used_refs)
        parent = node.get("parent_ref") or node.get("filteredparentid")
        role = str(node.get("role") or "")
        bounds = node.get("bounds") or {}
        x = int(node.get("x", bounds.get("x", 0)) or 0)
        y = int(node.get("y", bounds.get("y", 0)) or 0)
        width = int(node.get("width", bounds.get("width", 0)) or 0)
        height = int(node.get("height", bounds.get("height", 0)) or 0)
        descriptor = dict(node.get("descriptor") or _descriptor_from_node(node))
        candidates = list(node.get("locator_candidates") or repo_identity.locator_candidates(node))
        entry = {
            **node,
            "element_ref": element_ref,
            "parent_ref": parent,
            "raw_node_id": str(node.get("elementid") or node.get("raw_node_id") or ""),
            "friendly_name": str(node.get("friendly_name") or node.get("name") or element_ref),
            "role": role,
            "included": bool(node.get("included", True)),
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "bounds": {"x": x, "y": y, "width": width, "height": height},
            "descriptor": descriptor,
            "locator_candidates": candidates,
            "source": str(node.get("source") or "recording"),
            "status": str(node.get("status") or "active"),
            "confidence": float(node.get("confidence") or 1.0),
            "created_at": str(node.get("created_at") or now),
            "updated_at": now,
        }
        normalized.append(entry)
    return normalized


def load_snapshot_db(snapshot_db: Path | str) -> list[dict]:
    """Load a recording snapshot.db into element dictionaries."""
    conn = sqlite3.connect(str(snapshot_db))
    try:
        rows = conn.execute(
            """
            SELECT elementid, name, role, description, xpath, x, y,
                   width, height, states, text, filteredparentid
            FROM page_snapshot
            ORDER BY elementid
            """
        ).fetchall()
    finally:
        conn.close()

    import json
    elements: list[dict] = []
    for row in rows:
        states_raw = row[9] or "[]"
        try:
            states = json.loads(states_raw) if isinstance(states_raw, str) else states_raw
        except json.JSONDecodeError:
            states = []
        elements.append({
            "elementid": row[0] or "",
            "name": row[1] or "",
            "role": row[2] or "",
            "description": row[3] or "",
            "xpath": row[4] or "",
            "x": int(row[5] or 0),
            "y": int(row[6] or 0),
            "width": int(row[7] or 0),
            "height": int(row[8] or 0),
            "states": states,
            "text": row[10] or "",
            "filteredparentid": row[11],
        })
    return elements


# ── Index ────────────────────────────────────────────────────────────────────

def load_index(repo_dir: Path = config.REPO_DIR) -> dict:
    """Return the full index dict; empty dict if missing."""
    data = _yaml_load(repo_dir / "index.yaml")
    if data is None:
        return {"forms": {}, "flows": {}}
    return data


def save_index(index: dict, repo_dir: Path = config.REPO_DIR) -> None:
    _yaml_save(repo_dir / "index.yaml", index)


def register_fingerprint(fingerprint: str, form_id: str, repo_dir: Path = config.REPO_DIR) -> None:
    """Map a fingerprint hash to a form_id in the index."""
    form = load_form(form_id, repo_dir)
    if form:
        form["fingerprint"] = fingerprint
        save_form(form, repo_dir)
    index = load_index(repo_dir)
    index.setdefault("forms", {})[fingerprint] = form_id
    save_index(index, repo_dir)


def lookup_fingerprint(fingerprint: str, repo_dir: Path = config.REPO_DIR) -> str | None:
    """Return form_id for a known fingerprint, or None."""
    with _db_connect(repo_dir) as conn:
        row = conn.execute(
            "SELECT id FROM forms WHERE fingerprint=?",
            (fingerprint,),
        ).fetchone()
        if row:
            return str(row["id"])
    return load_index(repo_dir).get("forms", {}).get(fingerprint)


def register_flow(flow_name: str, flow_id: str, repo_dir: Path = config.REPO_DIR) -> None:
    index = load_index(repo_dir)
    index.setdefault("flows", {})[flow_name] = flow_id
    save_index(index, repo_dir)


def lookup_flow(flow_name: str, repo_dir: Path = config.REPO_DIR) -> str | None:
    return load_index(repo_dir).get("flows", {}).get(flow_name)


# ── Forms ────────────────────────────────────────────────────────────────────

def load_form(form_id: str, repo_dir: Path = config.REPO_DIR) -> dict | None:
    with _db_connect(repo_dir) as conn:
        row = conn.execute("SELECT data_json FROM forms WHERE id=?", (form_id,)).fetchone()
    if row:
        return _json_load(row["data_json"])
    return _yaml_load(repo_dir / "forms" / f"{form_id}.yaml")


def save_form(form: dict, repo_dir: Path = config.REPO_DIR) -> None:
    """
    form must have 'id' key.
    Expected keys: id, surface (html|java), title, function_id (optional), first_seen, confirmed_by.
    """
    form.setdefault("first_seen", _now_iso())
    now = _now_iso()
    form_id = form["id"]
    with _db_connect(repo_dir) as conn:
        conn.execute(
            """
            INSERT INTO forms (
                id, surface, title, fingerprint, screenshot, first_seen, updated_at, data_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                surface=excluded.surface,
                title=excluded.title,
                fingerprint=excluded.fingerprint,
                screenshot=excluded.screenshot,
                updated_at=excluded.updated_at,
                data_json=excluded.data_json
            """,
            (
                form_id,
                form.get("surface", ""),
                form.get("title", ""),
                form.get("fingerprint"),
                form.get("screenshot"),
                form.get("first_seen"),
                now,
                _json_dump(form),
            ),
        )
        conn.commit()


def update_form(form_id: str, updates: dict, repo_dir: Path = config.REPO_DIR) -> dict:
    """Merge updates into a form metadata record and return the saved form."""
    form = load_form(form_id, repo_dir) or {"id": form_id}
    form.update(updates)
    save_form(form, repo_dir)
    return form


def list_form_ids(repo_dir: Path = config.REPO_DIR) -> list[str]:
    ids: set[str] = set()
    with _db_connect(repo_dir) as conn:
        ids.update(str(row["id"]) for row in conn.execute("SELECT id FROM forms ORDER BY id"))
    forms_dir = repo_dir / "forms"
    if forms_dir.exists():
        ids.update(p.stem for p in forms_dir.glob("*.yaml"))
    return sorted(ids)


# ── Elements ─────────────────────────────────────────────────────────────────

def load_elements(form_id: str, repo_dir: Path = config.REPO_DIR) -> list[dict]:
    """Return element list for a form; empty list if not catalogued yet."""
    with _db_connect(repo_dir) as conn:
        rows = conn.execute(
            """
            SELECT data_json FROM elements
            WHERE form_id=?
            ORDER BY CASE
                WHEN elementid GLOB 'e[0-9]*' THEN CAST(substr(elementid, 2) AS INTEGER)
                ELSE 1000000000
            END, elementid
            """,
            (form_id,),
        ).fetchall()
    if rows:
        return [_json_load(row["data_json"]) for row in rows]
    data = _yaml_load(repo_dir / "elements" / f"{form_id}.yaml")
    return data if isinstance(data, list) else []


def save_elements(form_id: str, elements: list[dict], repo_dir: Path = config.REPO_DIR) -> None:
    if not load_form(form_id, repo_dir):
        save_form({"id": form_id, "surface": "java", "title": form_id}, repo_dir)
    with _db_connect(repo_dir) as conn:
        conn.execute("DELETE FROM elements WHERE form_id=?", (form_id,))
        for element in elements:
            elementid = str(element.get("elementid") or element.get("friendly_name") or "").strip()
            if not elementid:
                continue
            bounds = element.get("bounds") or {}
            x = int(element.get("x", bounds.get("x", 0)) or 0)
            y = int(element.get("y", bounds.get("y", 0)) or 0)
            width = int(element.get("width", bounds.get("width", 0)) or 0)
            height = int(element.get("height", bounds.get("height", 0)) or 0)
            element = {
                **element,
                "elementid": elementid,
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "bounds": {"x": x, "y": y, "width": width, "height": height},
            }
            conn.execute(
                """
                INSERT INTO elements (
                    form_id, elementid, friendly_name, role, name, description,
                    xpath, x, y, width, height, enabled, confirmed_by,
                    first_seen, last_seen, data_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    form_id,
                    elementid,
                    element.get("friendly_name", elementid),
                    element.get("role", ""),
                    element.get("name", ""),
                    element.get("description", ""),
                    element.get("xpath", ""),
                    x,
                    y,
                    width,
                    height,
                    0 if element.get("enabled") is False else 1,
                    element.get("confirmed_by", ""),
                    element.get("first_seen"),
                    element.get("last_seen"),
                    _json_dump(element),
                ),
            )
        conn.commit()


def save_form_capture(
    form_id: str,
    elements: list[dict],
    *,
    screenshot_path: Path | str | None = None,
    source: str = "recording",
    repo_dir: Path = config.REPO_DIR,
) -> None:
    """Persist a full captured form inventory into the object repository.

    This is the durable replacement for relying on a per-recording snapshot.db
    during replay. Every captured Java-agent element keeps its eXX ref, path, screen
    bounds, states, text, and an ``enabled`` flag for future review UI filtering.
    Existing human-friendly names, aliases, confirmation status, and enablement
    are preserved when a capture is refreshed.
    """
    existing = load_elements(form_id, repo_dir)
    by_elementid = {el.get("elementid"): el for el in existing if el.get("elementid")}
    by_xpath = {el.get("xpath"): el for el in existing if el.get("xpath")}
    by_uid = {el.get("element_uid"): el for el in existing if el.get("element_uid")}
    by_identity = {
        el.get("identity_key") or repo_identity.element_identity_key(form_id, el): el
        for el in existing
    }
    now = _now_iso()

    catalog: list[dict] = []
    seen_existing_keys: set[str] = set()
    used_refs: set[str] = set()
    for raw in elements:
        elementid = str(raw.get("elementid") or "").strip()
        if not elementid:
            continue
        identity_key = repo_identity.element_identity_key(form_id, raw)
        element_uid = repo_identity.element_uid(form_id, raw)
        previous = (
            by_uid.get(element_uid)
            or by_identity.get(identity_key)
            or by_elementid.get(elementid)
            or by_xpath.get(raw.get("xpath"))
            or {}
        )
        previous_key = previous.get("element_uid") or previous.get("identity_key")
        if previous_key:
            seen_existing_keys.add(str(previous_key))

        base_ref = previous.get("semantic_ref") or previous.get("friendly_name") or repo_identity.semantic_ref(raw)
        semantic_ref = _unique_semantic_ref(str(base_ref), used_refs)
        used_refs.add(semantic_ref)

        bounds = {
            "x": int(raw.get("x") or 0),
            "y": int(raw.get("y") or 0),
            "width": int(raw.get("width") or 0),
            "height": int(raw.get("height") or 0),
        }
        states = raw.get("states") or []
        if isinstance(states, str):
            try:
                import json
                states = json.loads(states)
            except Exception:
                states = []
        record = {
            "elementid": elementid,
            "element_uid": previous.get("element_uid") or element_uid,
            "identity_key": previous.get("identity_key") or identity_key,
            "semantic_ref": semantic_ref,
            "friendly_name": previous.get("friendly_name") or semantic_ref,
            "aliases": previous.get("aliases", []),
            "enabled": previous.get("enabled", True),
            "role": raw.get("role", ""),
            "name": raw.get("name", ""),
            "description": raw.get("description", ""),
            "xpath": raw.get("xpath", ""),
            "bounds": bounds,
            # Keep flat coordinates for existing resolvers and generated code.
            "x": bounds["x"],
            "y": bounds["y"],
            "width": bounds["width"],
            "height": bounds["height"],
            "states": states,
            "text": raw.get("text", ""),
            "filteredparentid": raw.get("filteredparentid"),
            "ancestors": raw.get("ancestors", previous.get("ancestors", [])),
            "confirmed_by": previous.get("confirmed_by", "capture"),
            "first_seen": previous.get("first_seen", now),
            "last_seen": now,
            "capture_status": "active",
            "locator_candidates": repo_identity.merge_locator_candidates(
                previous.get("locator_candidates"),
                repo_identity.locator_candidates(raw),
            ),
            "source": source,
        }
        catalog.append(record)

    for previous in existing:
        previous_key = str(previous.get("element_uid") or previous.get("identity_key") or "")
        if previous_key and previous_key in seen_existing_keys:
            continue
        preserved = dict(previous)
        preserved.setdefault("semantic_ref", preserved.get("friendly_name") or preserved.get("elementid"))
        preserved.setdefault("element_uid", repo_identity.element_uid(form_id, preserved))
        preserved.setdefault("identity_key", repo_identity.element_identity_key(form_id, preserved))
        preserved["capture_status"] = "stale"
        preserved["source"] = preserved.get("source") or source
        preserved["locator_candidates"] = repo_identity.merge_locator_candidates(
            preserved.get("locator_candidates"),
            repo_identity.locator_candidates(preserved),
        )
        ref = str(preserved.get("semantic_ref") or preserved.get("friendly_name") or preserved.get("elementid"))
        if ref in used_refs:
            preserved["aliases"] = list({*preserved.get("aliases", []), ref})
            preserved["semantic_ref"] = _unique_semantic_ref(ref, used_refs)
        used_refs.add(str(preserved.get("semantic_ref")))
        catalog.append(preserved)

    catalog.sort(key=lambda el: (_element_number(el.get("elementid", "")), el.get("elementid", "")))
    save_elements(form_id, catalog, repo_dir)

    screenshot_rel = None
    if screenshot_path:
        src = Path(screenshot_path)
        if src.exists():
            screenshots_dir = repo_dir / "screenshots"
            screenshots_dir.mkdir(parents=True, exist_ok=True)
            dest = screenshots_dir / f"{form_id}{src.suffix or '.png'}"
            if src.resolve() != dest.resolve():
                shutil.copy2(src, dest)
            screenshot_rel = dest.relative_to(repo_dir).as_posix()

    capture_meta = {
        "last_captured": now,
        "element_count": len(catalog),
        "coordinate_space": "screen",
        "structure_hash": repo_identity.screen_structure_hash(catalog),
    }
    if screenshot_rel:
        capture_meta["screenshot"] = screenshot_rel
    form_updates: dict = {"capture": capture_meta}
    if screenshot_rel:
        form_updates["screenshot"] = screenshot_rel
    update_form(form_id, form_updates, repo_dir)


def _unique_semantic_ref(base_ref: str, used_refs: set[str]) -> str:
    ref = repo_identity.normalize_ref(base_ref)
    if ref not in used_refs:
        return ref
    index = 2
    while f"{ref}_{index}" in used_refs:
        index += 1
    return f"{ref}_{index}"


def _element_number(elementid: str) -> int:
    if elementid.startswith("e"):
        try:
            return int(elementid[1:])
        except ValueError:
            pass
    return 10**9


def list_containers(repo_dir: Path = config.REPO_DIR) -> list[dict]:
    """List all stored containers in the new container-first repository model."""
    with _db_connect(repo_dir) as conn:
        rows = conn.execute(
            """
            SELECT container_ref, title, surface, fingerprint, screenshot,
                   raw_dom_path, tree_path, status, source, created_at, updated_at, metadata_json
            FROM containers
            ORDER BY container_ref
            """
        ).fetchall()
    containers: list[dict] = []
    for row in rows:
        containers.append({
            "container_ref": row["container_ref"],
            "title": row["title"],
            "surface": row["surface"],
            "fingerprint": row["fingerprint"],
            "screenshot": row["screenshot"],
            "raw_dom_path": row["raw_dom_path"],
            "tree_path": row["tree_path"],
            "status": row["status"],
            "source": row["source"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "metadata": _json_load(row["metadata_json"]) or {},
        })
    return containers


def load_container(container_ref: str, repo_dir: Path = config.REPO_DIR) -> dict | None:
    """Load one container with persisted tree and raw-dom artifacts."""
    with _db_connect(repo_dir) as conn:
        row = conn.execute(
            """
            SELECT container_ref, title, surface, fingerprint, screenshot,
                   raw_dom_path, tree_path, status, source, created_at, updated_at, metadata_json
            FROM containers
            WHERE container_ref=?
            """,
            (container_ref,),
        ).fetchone()
        if row is None:
            return None
        element_rows = conn.execute(
            """
            SELECT data_json
            FROM container_elements
            WHERE container_ref=?
            ORDER BY element_ref
            """,
            (container_ref,),
        ).fetchall()

    repo_dir = Path(repo_dir)
    raw_dom: dict | None = None
    tree: list[dict] = [_json_load(r["data_json"]) for r in element_rows]

    raw_dom_rel = row["raw_dom_path"]
    if raw_dom_rel:
        raw_path = repo_dir / str(raw_dom_rel)
        if raw_path.exists():
            raw_dom = _json_load(raw_path.read_text(encoding="utf-8"))

    tree_rel = row["tree_path"]
    if tree_rel:
        tree_path = repo_dir / str(tree_rel)
        if tree_path.exists():
            try:
                file_tree = _json_load(tree_path.read_text(encoding="utf-8"))
                if isinstance(file_tree, list):
                    tree = file_tree
            except json.JSONDecodeError:
                pass

    return {
        "container_ref": row["container_ref"],
        "title": row["title"],
        "surface": row["surface"],
        "fingerprint": row["fingerprint"],
        "screenshot": row["screenshot"],
        "raw_dom_path": row["raw_dom_path"],
        "tree_path": row["tree_path"],
        "status": row["status"],
        "source": row["source"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "metadata": _json_load(row["metadata_json"]) or {},
        "raw_dom": raw_dom,
        "tree": tree,
    }


def save_container_tree(
    container_ref: str,
    tree_elements: list[dict],
    *,
    title: str | None = None,
    surface: str = "java_forms",
    fingerprint: str | None = None,
    screenshot: str | None = None,
    raw_dom_path: str | None = None,
    source: str = "recording",
    status: str = "active",
    metadata: dict | None = None,
    repo_dir: Path = config.REPO_DIR,
) -> dict:
    """Persist normalized container tree into the new repository tables/files."""
    repo_dir = Path(repo_dir)
    now = _now_iso()
    normalized = _normalize_tree_elements(tree_elements)
    artifacts_dir = _artifact_dir(container_ref, repo_dir)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    tree_file = artifacts_dir / "tree.json"
    tree_file.write_text(_json_dump(normalized), encoding="utf-8")
    tree_rel = tree_file.relative_to(repo_dir).as_posix()
    meta = dict(metadata or {})

    with _db_connect(repo_dir) as conn:
        existing = conn.execute(
            "SELECT created_at, title, surface, metadata_json, screenshot, raw_dom_path, fingerprint "
            "FROM containers WHERE container_ref=?",
            (container_ref,),
        ).fetchone()
        created_at = existing["created_at"] if existing else now
        merged_meta = dict(_json_load(existing["metadata_json"]) or {}) if existing else {}
        merged_meta.update(meta)

        conn.execute(
            """
            INSERT INTO containers (
                container_ref, title, surface, fingerprint, screenshot, raw_dom_path, tree_path,
                status, source, created_at, updated_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(container_ref) DO UPDATE SET
                title=excluded.title,
                surface=excluded.surface,
                fingerprint=COALESCE(excluded.fingerprint, containers.fingerprint),
                screenshot=COALESCE(excluded.screenshot, containers.screenshot),
                raw_dom_path=COALESCE(excluded.raw_dom_path, containers.raw_dom_path),
                tree_path=excluded.tree_path,
                status=excluded.status,
                source=excluded.source,
                updated_at=excluded.updated_at,
                metadata_json=excluded.metadata_json
            """,
            (
                container_ref,
                title or (existing["title"] if existing else container_ref),
                surface or (existing["surface"] if existing else "java_forms"),
                fingerprint if fingerprint is not None else (existing["fingerprint"] if existing else None),
                screenshot if screenshot is not None else (existing["screenshot"] if existing else None),
                raw_dom_path if raw_dom_path is not None else (existing["raw_dom_path"] if existing else None),
                tree_rel,
                status,
                source,
                created_at,
                now,
                _json_dump(merged_meta),
            ),
        )

        conn.execute("DELETE FROM container_elements WHERE container_ref=?", (container_ref,))
        for element in normalized:
            bounds = element.get("bounds") or {}
            conn.execute(
                """
                INSERT INTO container_elements (
                    container_ref, element_ref, parent_ref, raw_node_id, friendly_name, role,
                    included, x, y, width, height, screen_x, screen_y, screen_width, screen_height,
                    source, status, confidence, descriptor_json, locator_candidates_json,
                    metadata_json, data_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    container_ref,
                    element["element_ref"],
                    element.get("parent_ref"),
                    element.get("raw_node_id"),
                    element.get("friendly_name") or element["element_ref"],
                    element.get("role") or "",
                    0 if element.get("included") is False else 1,
                    int(element.get("x", bounds.get("x", 0)) or 0),
                    int(element.get("y", bounds.get("y", 0)) or 0),
                    int(element.get("width", bounds.get("width", 0)) or 0),
                    int(element.get("height", bounds.get("height", 0)) or 0),
                    int(element.get("x", bounds.get("x", 0)) or 0),
                    int(element.get("y", bounds.get("y", 0)) or 0),
                    int(element.get("width", bounds.get("width", 0)) or 0),
                    int(element.get("height", bounds.get("height", 0)) or 0),
                    element.get("source") or source,
                    element.get("status") or status,
                    float(element.get("confidence") or 1.0),
                    _json_dump(element.get("descriptor") or {}),
                    _json_dump(element.get("locator_candidates") or []),
                    _json_dump(element.get("metadata") or {}),
                    _json_dump(element),
                    element.get("created_at") or now,
                    now,
                ),
            )
        conn.commit()

    saved = load_container(container_ref, repo_dir)
    if saved is None:
        raise RuntimeError(f"Failed to load saved container {container_ref!r}")
    return saved


def save_container_scan(
    container_ref: str,
    *,
    title: str,
    raw_dom: dict,
    tree_elements: list[dict],
    screenshot_path: str | Path,
    surface: str = "java_forms",
    source: str = "recording",
    status: str = "active",
    metadata: dict | None = None,
    repo_dir: Path = config.REPO_DIR,
) -> dict:
    """Persist raw DOM, editable tree, and screenshot for a container."""
    repo_dir = Path(repo_dir)
    artifacts_dir = _artifact_dir(container_ref, repo_dir)
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    raw_file = artifacts_dir / "raw_dom.json"
    raw_file.write_text(_json_dump(raw_dom), encoding="utf-8")
    raw_rel = raw_file.relative_to(repo_dir).as_posix()

    src = Path(screenshot_path)
    screenshots_dir = repo_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    screenshot_dest = screenshots_dir / f"{repo_identity.normalize_ref(container_ref)}{src.suffix or '.png'}"
    if src.exists() and src.resolve() != screenshot_dest.resolve():
        shutil.copy2(src, screenshot_dest)
    screenshot_rel = screenshot_dest.relative_to(repo_dir).as_posix()

    return save_container_tree(
        container_ref,
        tree_elements,
        title=title,
        surface=surface,
        screenshot=screenshot_rel,
        raw_dom_path=raw_rel,
        source=source,
        status=status,
        metadata=metadata,
        repo_dir=repo_dir,
    )


def upsert_container_element(
    container_ref: str,
    element_ref: str,
    updates: dict,
    repo_dir: Path = config.REPO_DIR,
) -> dict:
    """Update or insert a single element inside a container tree."""
    container = load_container(container_ref, repo_dir)
    if container is None:
        raise KeyError(f"Unknown container_ref {container_ref!r}")
    tree = list(container.get("tree") or [])
    found = False
    for idx, element in enumerate(tree):
        if str(element.get("element_ref") or "") == element_ref:
            merged = {**element, **updates, "element_ref": element_ref}
            tree[idx] = merged
            found = True
            break
    if not found:
        tree.append({"element_ref": element_ref, **updates})
    return save_container_tree(
        container_ref,
        tree,
        title=container.get("title") or container_ref,
        surface=container.get("surface") or "java_forms",
        fingerprint=container.get("fingerprint"),
        screenshot=container.get("screenshot"),
        raw_dom_path=container.get("raw_dom_path"),
        source=container.get("source") or "manual",
        status=container.get("status") or "active",
        metadata=container.get("metadata") or {},
        repo_dir=repo_dir,
    )


def delete_container_element(
    container_ref: str,
    element_ref: str,
    repo_dir: Path = config.REPO_DIR,
) -> dict:
    """Delete one element from a container tree."""
    container = load_container(container_ref, repo_dir)
    if container is None:
        raise KeyError(f"Unknown container_ref {container_ref!r}")
    tree = [
        element
        for element in (container.get("tree") or [])
        if str(element.get("element_ref") or "") != element_ref
    ]
    return save_container_tree(
        container_ref,
        tree,
        title=container.get("title") or container_ref,
        surface=container.get("surface") or "java_forms",
        fingerprint=container.get("fingerprint"),
        screenshot=container.get("screenshot"),
        raw_dom_path=container.get("raw_dom_path"),
        source=container.get("source") or "manual",
        status=container.get("status") or "active",
        metadata=container.get("metadata") or {},
        repo_dir=repo_dir,
    )


def find_element_by_ref(
    form_id: str,
    ref: str,
    repo_dir: Path = config.REPO_DIR,
    *,
    include_disabled: bool = False,
) -> dict | None:
    """Return an element by semantic ref, eXX ref, friendly_name, alias, uid, or raw name."""
    for el in load_elements(form_id, repo_dir):
        if not include_disabled and el.get("enabled") is False:
            continue
        if el.get("semantic_ref") == ref:
            return el
        if el.get("elementid") == ref:
            return el
        if el.get("element_uid") == ref:
            return el
        if el.get("friendly_name") == ref:
            return el
        if ref in el.get("aliases", []):
            return el
        if el.get("name") == ref:
            return el
    return None


def split_target_ref(ref: str, default_form_id: str | None = None) -> tuple[str | None, str]:
    """Split refs like ``find_orders.order_type`` into form and element parts."""
    ref = str(ref or "").strip()
    if "." in ref:
        form_ref, element_ref = ref.split(".", 1)
        return form_ref or default_form_id, element_ref
    return default_form_id, ref


def resolve_element_ref(
    ref: str,
    form_id: str | None = None,
    repo_dir: Path = config.REPO_DIR,
    *,
    include_disabled: bool = False,
) -> tuple[str, dict] | None:
    """Resolve a user-friendly ref to ``(form_id, element)``.

    Supports both scoped refs (``java_find_orders.order_type``) and legacy refs
    where ``form_id`` is supplied separately.
    """
    resolved_form_id, element_ref = split_target_ref(ref, form_id)
    if resolved_form_id:
        element = find_element_by_ref(
            resolved_form_id,
            element_ref,
            repo_dir,
            include_disabled=include_disabled,
        )
        return (resolved_form_id, element) if element else None

    matches: list[tuple[str, dict]] = []
    for candidate_form_id in list_form_ids(repo_dir):
        element = find_element_by_ref(
            candidate_form_id,
            element_ref,
            repo_dir,
            include_disabled=include_disabled,
        )
        if element:
            matches.append((candidate_form_id, element))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        forms = ", ".join(form for form, _ in matches)
        raise RuntimeError(f"Ambiguous repo ref {ref!r}; found in forms: {forms}")
    return None


def element_public_ref(form_id: str, element: dict) -> str:
    """Return the stable script-facing ``form.element`` ref for an element."""
    return f"{form_id}.{element.get('semantic_ref') or element.get('friendly_name') or element.get('elementid')}"


def upsert_actioned_element(
    form_id: str,
    element: dict,
    repo_dir: Path = config.REPO_DIR,
    *,
    source: str = "recording_action",
) -> dict:
    """Add or refresh one element that was actually actioned by the user/model.

    This intentionally avoids saving a full screen inventory. Future recordings
    can add more actioned elements to the same screen without duplicating or
    deleting previously reviewed definitions.
    """
    if not load_form(form_id, repo_dir):
        save_form({"id": form_id, "surface": "java", "title": form_id}, repo_dir)

    now = _now_iso()
    identity_key = repo_identity.element_identity_key(form_id, element)
    element_uid = repo_identity.element_uid(form_id, element)
    elements = load_elements(form_id, repo_dir)
    match_index: int | None = None
    for index, existing in enumerate(elements):
        existing_key = existing.get("identity_key") or repo_identity.element_identity_key(form_id, existing)
        if (
            existing.get("element_uid") == element_uid
            or existing_key == identity_key
            or (
                existing.get("elementid")
                and element.get("elementid")
                and existing.get("elementid") == element.get("elementid")
            )
        ):
            match_index = index
            break

    previous = elements[match_index] if match_index is not None else {}
    semantic_ref = previous.get("semantic_ref") or repo_identity.semantic_ref(element)
    record = {
        **previous,
        **element,
        "element_uid": previous.get("element_uid") or element_uid,
        "identity_key": previous.get("identity_key") or identity_key,
        "semantic_ref": semantic_ref,
        "friendly_name": previous.get("friendly_name") or semantic_ref,
        "aliases": previous.get("aliases", element.get("aliases", [])),
        "enabled": previous.get("enabled", element.get("enabled", True)),
        "confirmed_by": previous.get("confirmed_by", element.get("confirmed_by", "capture")),
        "first_seen": previous.get("first_seen", now),
        "last_seen": now,
        "capture_status": "active",
        "source": source,
        "locator_candidates": repo_identity.merge_locator_candidates(
            previous.get("locator_candidates"),
            repo_identity.locator_candidates(element),
        ),
    }
    if not record.get("elementid"):
        record["elementid"] = record["semantic_ref"]

    if match_index is None:
        used_refs = {str(el.get("semantic_ref")) for el in elements if el.get("semantic_ref")}
        if record["semantic_ref"] in used_refs:
            record["semantic_ref"] = _unique_semantic_ref(record["semantic_ref"], used_refs)
            record["friendly_name"] = record["semantic_ref"]
        elements.append(record)
    else:
        elements[match_index] = record
    save_elements(form_id, elements, repo_dir)
    return record


def find_element(form_id: str, friendly_name: str, repo_dir: Path = config.REPO_DIR) -> dict | None:
    """Return the element record matching friendly_name (or any alias), or None."""
    return find_element_by_ref(form_id, friendly_name, repo_dir)


def upsert_element(form_id: str, element: dict, repo_dir: Path = config.REPO_DIR) -> str:
    """
    Add a new element or update aliases on an existing one.

    Returns: "added" | "alias_added" | "unchanged"

    Conflict policy:
      - If an element with the same (role, name) exists under a DIFFERENT friendly_name,
        add the new friendly_name as an alias and emit a warning — never overwrite.
      - If the friendly_name matches exactly, update mutable fields (xpath, bounds) and
        add new aliases.
    """
    element.setdefault("first_seen", _now_iso())
    element.setdefault("aliases", [])
    element.setdefault("confirmed_by", "ai")

    elements = load_elements(form_id, repo_dir)
    fn = element["friendly_name"]
    role, name = element.get("role"), element.get("name")

    # Exact friendly_name match → update in place
    for i, existing in enumerate(elements):
        if existing["friendly_name"] == fn:
            # Merge aliases
            merged_aliases = list({*existing.get("aliases", []), *element.get("aliases", [])})
            elements[i] = {**existing, **element, "aliases": merged_aliases,
                           "first_seen": existing.get("first_seen", element["first_seen"])}
            save_elements(form_id, elements, repo_dir)
            return "unchanged"

    # Same (role, name) under a different friendly_name → add as alias + warn
    for i, existing in enumerate(elements):
        if existing.get("role") == role and existing.get("name") == name:
            if fn not in existing.get("aliases", []):
                existing.setdefault("aliases", []).append(fn)
                elements[i] = existing
                save_elements(form_id, elements, repo_dir)
                import warnings
                warnings.warn(
                    f"[qcs_repo] Element (role={role!r}, name={name!r}) already exists as "
                    f"{existing['friendly_name']!r} in form {form_id!r}. "
                    f"Added {fn!r} as alias. Possible EBS label drift.",
                    stacklevel=2,
                )
                return "alias_added"
            return "unchanged"

    # Genuinely new
    elements.append(element)
    save_elements(form_id, elements, repo_dir)
    return "added"


# ── Flows ─────────────────────────────────────────────────────────────────────

def load_flow(flow_id: str, repo_dir: Path = config.REPO_DIR) -> dict | None:
    return _yaml_load(repo_dir / "flows" / f"{flow_id}.yaml")


def save_flow(flow: dict, repo_dir: Path = config.REPO_DIR) -> None:
    """flow must have 'id' key."""
    flow.setdefault("first_seen", _now_iso())
    _yaml_save(repo_dir / "flows" / f"{flow['id']}.yaml", flow)
    register_flow(flow.get("name", flow["id"]), flow["id"], repo_dir)


def list_flow_ids(repo_dir: Path = config.REPO_DIR) -> list[str]:
    flows_dir = repo_dir / "flows"
    if not flows_dir.exists():
        return []
    return [p.stem for p in sorted(flows_dir.glob("*.yaml"))]


# ── Repo-patch (healer output) ───────────────────────────────────────────────

def load_repo_patch(patch_path: Path) -> list[dict]:
    data = _yaml_load(patch_path)
    return data if isinstance(data, list) else []


def append_repo_patch(patch_path: Path, entry: dict) -> None:
    """Append a single patch entry to the patch file (for healer output)."""
    existing = load_repo_patch(patch_path)
    existing.append({**entry, "patched_at": _now_iso()})
    _yaml_save(patch_path, existing)


# ── Form-scoped object repository entries ─────────────────────────────────────

def _row_to_entry(row: sqlite3.Row) -> "RepoEntry":
    from qcs_repo.schema import RepoEntry  # noqa: PLC0415
    import json as _json  # noqa: PLC0415
    return RepoEntry.from_dict({
        "form_ref": row["form_ref"],
        "element_ref": row["element_ref"],
        "qualified_ref": row["qualified_ref"],
        "friendly_name": row["friendly_name"],
        "surface": row["surface"],
        "object_type": row["object_type"],
        "descriptor": _json.loads(row["descriptor_json"] or "{}"),
        "fallback_descriptors": _json.loads(row["fallback_descriptors_json"] or "[]"),
        "source": row["source"],
        "confidence": row["confidence"],
        "status": row["status"],
        "last_validated_run": row["last_validated_run"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "metadata": _json.loads(row["metadata_json"] or "{}"),
    })


def upsert_entry(entry: "RepoEntry", repo_dir: Path = config.REPO_DIR) -> None:
    """Validate and upsert a RepoEntry.  Raises RepoValidationError on invalid data."""
    from qcs_repo.schema import validate_entry, RepoValidationError  # noqa: PLC0415
    errors = validate_entry(entry.to_dict())
    if errors:
        raise RepoValidationError(errors)
    now = _now_iso()
    entry.updated_at = now
    entry.qualified_ref = f"{entry.form_ref}.{entry.element_ref}"
    with _db_connect(repo_dir) as conn:
        conn.execute(
            """
            INSERT INTO repo_entries (
                form_ref, element_ref, qualified_ref, friendly_name, surface, object_type,
                descriptor_json, fallback_descriptors_json, source, confidence, status,
                last_validated_run, created_at, updated_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(form_ref, element_ref) DO UPDATE SET
                qualified_ref=excluded.qualified_ref,
                friendly_name=excluded.friendly_name,
                surface=excluded.surface,
                object_type=excluded.object_type,
                descriptor_json=excluded.descriptor_json,
                fallback_descriptors_json=excluded.fallback_descriptors_json,
                source=excluded.source,
                confidence=excluded.confidence,
                status=excluded.status,
                last_validated_run=excluded.last_validated_run,
                updated_at=excluded.updated_at,
                metadata_json=excluded.metadata_json
            """,
            (
                entry.form_ref, entry.element_ref, entry.qualified_ref, entry.friendly_name,
                entry.surface, entry.object_type,
                _json_dump(entry.descriptor), _json_dump(entry.fallback_descriptors),
                entry.source, entry.confidence, entry.status,
                entry.last_validated_run, entry.created_at, now, _json_dump(entry.metadata),
            ),
        )
        conn.commit()


def load_entry(
    form_ref: str, element_ref: str, repo_dir: Path = config.REPO_DIR
) -> "RepoEntry | None":
    """Primary-key lookup: return the RepoEntry for (form_ref, element_ref) or None."""
    with _db_connect(repo_dir) as conn:
        row = conn.execute(
            "SELECT * FROM repo_entries WHERE form_ref=? AND element_ref=?",
            (form_ref, element_ref),
        ).fetchone()
    if row is None:
        return None
    return _row_to_entry(row)


def list_form_entries(
    form_ref: str, repo_dir: Path = config.REPO_DIR
) -> list["RepoEntry"]:
    """Return all RepoEntry rows for a given form_ref, ordered by element_ref."""
    with _db_connect(repo_dir) as conn:
        rows = conn.execute(
            "SELECT * FROM repo_entries WHERE form_ref=? ORDER BY element_ref",
            (form_ref,),
        ).fetchall()
    return [_row_to_entry(r) for r in rows]


def all_entries(repo_dir: Path = config.REPO_DIR) -> list["RepoEntry"]:
    """Return every RepoEntry in the repository, ordered by (form_ref, element_ref)."""
    with _db_connect(repo_dir) as conn:
        rows = conn.execute(
            "SELECT * FROM repo_entries ORDER BY form_ref, element_ref"
        ).fetchall()
    return [_row_to_entry(r) for r in rows]


def validate_repo(repo_dir: Path = config.REPO_DIR) -> list[str]:
    """Validate all repo_entries.

    Returns a list of formatted error strings.  An empty list means all entries
    are valid.  Each error is prefixed with the qualified_ref of the offending
    entry so errors are easy to locate::

        "[java_find.order_type] $.surface must be one of ..."
    """
    from qcs_repo.schema import validate_entry  # noqa: PLC0415
    all_errors: list[str] = []
    for entry in all_entries(repo_dir):
        entry_errors = validate_entry(entry.to_dict())
        for err in entry_errors:
            all_errors.append(f"[{entry.qualified_ref}] {err}")
    return all_errors


# ── Bulk form-module upsert ───────────────────────────────────────────────────

def upsert_form_module(
    form_ref: str,
    elements: list[dict],
    source: str = "recording",
    repo_dir: Path = config.REPO_DIR,
) -> list:  # list[RepoEntry]
    """Capture a complete form module (Tosca-style) into the object repository.

    Maps every element in *elements* to a ``RepoEntry`` keyed by
    ``(form_ref, element_ref)``, where ``element_ref`` is the stable semantic
    label derived from the element's human-readable display name via
    ``repo_identity.semantic_ref`` (e.g. ``"po_number"``, ``"find_button"``).
    Volatile capture-local ids such as ``e12`` are never used as the key.

    Preservation policy
    -------------------
    * ``created_at`` and ``status`` are **never** changed after the first
      insert for a given ``(form_ref, element_ref)`` pair.  Only
      ``descriptor_json``, ``friendly_name``, ``surface``, ``object_type``,
      ``source``, and ``updated_at`` are refreshed on subsequent scans.
    * Elements **absent** from *elements* are left completely untouched — no
      auto-deprecation.  They keep whatever status they had before.
    * New (first-seen) elements are inserted with ``status='candidate'``.
      They become ``'active'`` only when ``upsert_actioned_element`` is called
      for them, establishing that the user/model explicitly interacted with them.

    Uniqueness
    ----------
    When two elements in the same batch produce the same semantic label (rare
    in Oracle Forms but possible for duplicate field titles), the later element
    in iteration order receives a disambiguating ``_2``, ``_3`` suffix.
    Since ``build_action_context`` orders elements by Java DOM node id (stable
    across JVM restarts), disambiguation is deterministic across scans.

    Returns
    -------
    list[RepoEntry]
        The ``RepoEntry`` objects that were inserted or updated, one per
        element in *elements*.
    """
    if not elements:
        return []

    now = _now_iso()

    # Derive a stable, unique element_ref for each element in this batch.
    used_refs: set[str] = set()
    ref_pairs: list[tuple[str, dict]] = []
    for element in elements:
        base = repo_identity.semantic_ref(element)
        ref = _unique_semantic_ref(base, used_refs)
        used_refs.add(ref)
        ref_pairs.append((ref, element))

    with _db_connect(repo_dir) as conn:
        for element_ref, element in ref_pairs:
            friendly_name = (
                element.get("semantic_ref")
                or element.get("friendly_name")
                or element_ref
            )
            conn.execute(
                """
                INSERT INTO repo_entries (
                    form_ref, element_ref, qualified_ref, friendly_name, surface, object_type,
                    descriptor_json, fallback_descriptors_json, source, confidence, status,
                    last_validated_run, created_at, updated_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(form_ref, element_ref) DO UPDATE SET
                    qualified_ref  = excluded.qualified_ref,
                    friendly_name  = excluded.friendly_name,
                    surface        = excluded.surface,
                    object_type    = excluded.object_type,
                    descriptor_json= excluded.descriptor_json,
                    source         = excluded.source,
                    updated_at     = excluded.updated_at
                """,
                (
                    form_ref,
                    element_ref,
                    f"{form_ref}.{element_ref}",
                    friendly_name,
                    "java_forms",
                    str(element.get("role") or ""),
                    _json_dump(element),
                    "[]",           # fallback_descriptors_json
                    source,
                    1.0,            # confidence
                    "candidate",    # status — only for new rows; preserved on conflict
                    None,           # last_validated_run
                    now,            # created_at — kept from original on conflict (not in DO UPDATE SET)
                    now,            # updated_at — always refreshed on conflict
                    "{}",           # metadata_json
                ),
            )
        conn.commit()

        if ref_pairs:
            placeholders = ",".join("?" for _ in ref_pairs)
            rows = conn.execute(
                f"SELECT * FROM repo_entries "
                f"WHERE form_ref=? AND element_ref IN ({placeholders})",
                [form_ref, *[r for r, _ in ref_pairs]],
            ).fetchall()
        else:
            rows = []

    return [_row_to_entry(r) for r in rows]


def actioned_element_ref(
    form_ref: str,
    actioned_element: dict,
    batch_elements: list[dict],
) -> str | None:
    """Return the element_ref that :func:`upsert_form_module` commits for ``actioned_element``.

    Replays the identical ``repo_identity.semantic_ref`` + ``_unique_semantic_ref``
    disambiguation pass over *batch_elements* (in iteration order) that
    ``upsert_form_module`` uses internally, and returns the ref assigned to the
    element that is identity-equal to *actioned_element*.

    Identity is matched via ``repo_identity.element_uid(form_ref, el)`` for
    structurally-equivalent elements, with an ``el is actioned_element`` object-
    identity fallback for newly-constructed dicts not yet persisted.

    Returns ``None`` if *actioned_element* is not found in *batch_elements*.

    This function makes **no database writes**.
    """
    actioned_uid = repo_identity.element_uid(form_ref, actioned_element)
    used_refs: set[str] = set()
    for element in batch_elements:
        base = repo_identity.semantic_ref(element)
        ref = _unique_semantic_ref(base, used_refs)
        used_refs.add(ref)
        uid = repo_identity.element_uid(form_ref, element)
        if uid == actioned_uid or element is actioned_element:
            return ref
    return None
