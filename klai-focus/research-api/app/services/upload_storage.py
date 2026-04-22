"""Research-uploads volume storage + retention policy.

Layout: /opt/klai/research-uploads/{tenant_id}/{notebook_id}/{src_id}{ext}

Retention triggers (event-driven, no time-based sweep):

1. delete_when_source_removed
   - User clicks "remove source" in a notebook → DELETE /sources/{src_id}.
   - delete_one() unlinks the file before the DB row is dropped.

2. delete_when_notebook_removed
   - User deletes the whole notebook → DELETE /notebooks/{nb_id}.
   - cleanup_notebook() unlinks every file under that notebook's dir
     and removes the dir itself. Closes the orphan-files bug introduced
     by delete_notebook only purging Postgres+Qdrant.

3. delete_when_tenant_removed
   - When portal-api ever gains tenant teardown, it calls cleanup_tenant()
     (or, ops can run scripts/research_tenant_cleanup.py manually today).
   - Removes the entire /opt/klai/research-uploads/{tenant_id}/ tree.

The path layout is the only contract: tenant_id at depth 1, notebook_id
at depth 2. All cleanup operations rely on it; do not reorganise without
updating cleanup_notebook + cleanup_tenant.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

UPLOAD_BASE = Path("/opt/klai/research-uploads")


def _resolve_under_base(path: Path | str) -> Path | None:
    """Resolve a path and confirm it stays within UPLOAD_BASE.

    Defence against malicious or buggy callers passing absolute or escaping
    paths. Returns the resolved Path if safe; None otherwise.
    """
    candidate = Path(path).resolve()
    base = UPLOAD_BASE.resolve()
    try:
        candidate.relative_to(base)
        return candidate
    except ValueError:
        logger.warning("upload_path_outside_base", extra={"path": str(path)})
        return None


def save(tenant_id: str, notebook_id: str, src_id: str, ext: str, content: bytes) -> Path:
    """Save bytes for a new source. Returns the absolute file path stored on the row."""
    upload_dir = UPLOAD_BASE / tenant_id / notebook_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / f"{src_id}{ext}"
    file_path.write_bytes(content)
    return file_path


def delete_one(file_path: str | Path | None) -> bool:
    """Trigger 1: drop a single source's file from disk.

    Idempotent. Returns True if a file was actually removed (or was already
    absent at a valid path); False if the path was outside UPLOAD_BASE
    (rejected) or was None.
    """
    if not file_path:
        return False
    safe = _resolve_under_base(file_path)
    if safe is None:
        return False
    try:
        safe.unlink(missing_ok=True)
        return True
    except OSError as exc:
        logger.warning("upload_delete_failed", extra={"path": str(safe), "error": str(exc)})
        return False


def cleanup_notebook(tenant_id: str, notebook_id: str) -> int:
    """Trigger 2: drop every file under one notebook's dir and remove the dir.

    Used when a notebook is deleted. Idempotent — missing dir is a no-op.
    Returns the number of files removed.
    """
    if not tenant_id or not notebook_id:
        return 0
    nb_dir = _resolve_under_base(UPLOAD_BASE / tenant_id / notebook_id)
    if nb_dir is None or not nb_dir.exists():
        return 0
    count = 0
    for f in nb_dir.rglob("*"):
        if f.is_file():
            try:
                f.unlink()
                count += 1
            except OSError as exc:
                logger.warning("upload_cleanup_file_failed", extra={"path": str(f), "error": str(exc)})
    try:
        shutil.rmtree(nb_dir, ignore_errors=True)
    except OSError as exc:
        logger.warning("upload_cleanup_dir_failed", extra={"path": str(nb_dir), "error": str(exc)})
    return count


def cleanup_tenant(tenant_id: str) -> int:
    """Trigger 3: drop the entire tenant subtree.

    Called when a tenant is decommissioned. Returns the number of files
    removed. Idempotent. Refuses an empty tenant_id (would hit UPLOAD_BASE).
    """
    if not tenant_id:
        return 0
    tenant_dir = _resolve_under_base(UPLOAD_BASE / tenant_id)
    if tenant_dir is None or not tenant_dir.exists():
        return 0
    # Guard: tenant_dir must be a strict child of UPLOAD_BASE — never the base itself.
    if tenant_dir == UPLOAD_BASE.resolve():
        logger.error("upload_cleanup_tenant_refused_base", extra={"tenant_id": tenant_id})
        return 0
    count = sum(1 for f in tenant_dir.rglob("*") if f.is_file())
    shutil.rmtree(tenant_dir, ignore_errors=True)
    return count


def list_tenant_files(tenant_id: str) -> Iterable[Path]:
    """Read-only listing for ops/audit (used by the CLI cleanup script)."""
    tenant_dir = _resolve_under_base(UPLOAD_BASE / tenant_id)
    if tenant_dir is None or not tenant_dir.exists():
        return iter(())
    return (f for f in tenant_dir.rglob("*") if f.is_file())
