"""Research-uploads retention policy tests.

Policy (set 2026-04-22):
  Trigger 1 — file deleted when the user removes the source.
  Trigger 2 — all files in a notebook deleted when the notebook is removed.
  Trigger 3 — entire tenant subtree deleted when the tenant is decommissioned.

These tests pin the behaviour. If a future refactor breaks any trigger, one
of these tests turns red.

The tests do NOT exercise the FastAPI endpoint chain (which requires Postgres,
Qdrant, auth setup). They exercise the upload_storage helper module directly
— that's where the cleanup logic lives, and the endpoints are thin wrappers
around it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import upload_storage


@pytest.fixture
def upload_root(tmp_path, monkeypatch):
    """Redirect UPLOAD_BASE to a per-test tmp dir so the real volume is untouched."""
    monkeypatch.setattr(upload_storage, "UPLOAD_BASE", tmp_path)
    return tmp_path


def _seed_three_tenants(root: Path) -> dict[str, list[Path]]:
    """Build a realistic-ish layout: 3 tenants, 2 notebooks each, 2 files per nb."""
    layout: dict[str, list[Path]] = {}
    for tenant in ("tenant-A", "tenant-B", "tenant-C"):
        files: list[Path] = []
        for nb in ("nb-1", "nb-2"):
            for src in ("src-x", "src-y"):
                p = upload_storage.save(tenant, nb, src, ".pdf", b"fake-pdf-bytes")
                files.append(p)
        layout[tenant] = files
    return layout


# ── Trigger 1: source removed ────────────────────────────────────────────────

class TestDeleteOne:
    def test_unlinks_file(self, upload_root):
        p = upload_storage.save("t1", "nb1", "src1", ".pdf", b"x")
        assert p.exists()
        assert upload_storage.delete_one(p) is True
        assert not p.exists()

    def test_idempotent_on_missing_file(self, upload_root):
        p = upload_root / "t1" / "nb1" / "ghost.pdf"
        # Path does not exist; should not raise.
        assert upload_storage.delete_one(p) is True

    def test_none_is_noop(self):
        assert upload_storage.delete_one(None) is False

    def test_rejects_path_outside_base(self, upload_root, tmp_path):
        outside = tmp_path.parent / "evil.txt"
        outside.write_text("nope")
        assert upload_storage.delete_one(outside) is False
        # Untouched.
        assert outside.exists()
        outside.unlink()


# ── Trigger 2: notebook removed ──────────────────────────────────────────────

class TestCleanupNotebook:
    def test_removes_all_files_and_dir(self, upload_root):
        upload_storage.save("t1", "nb1", "src-a", ".pdf", b"x")
        upload_storage.save("t1", "nb1", "src-b", ".docx", b"y")
        upload_storage.save("t1", "nb1", "src-c", ".txt", b"z")
        nb_dir = upload_root / "t1" / "nb1"
        assert len(list(nb_dir.iterdir())) == 3

        removed = upload_storage.cleanup_notebook("t1", "nb1")
        assert removed == 3
        assert not nb_dir.exists()

    def test_only_touches_target_notebook(self, upload_root):
        upload_storage.save("t1", "nb1", "src-a", ".pdf", b"x")
        keep = upload_storage.save("t1", "nb-keep", "src-b", ".pdf", b"y")
        upload_storage.cleanup_notebook("t1", "nb1")
        assert keep.exists(), "sibling notebook must survive"

    def test_idempotent_on_missing_notebook(self, upload_root):
        assert upload_storage.cleanup_notebook("t1", "ghost-nb") == 0

    def test_empty_args_is_noop(self):
        assert upload_storage.cleanup_notebook("", "nb1") == 0
        assert upload_storage.cleanup_notebook("t1", "") == 0


# ── Trigger 3: tenant removed ────────────────────────────────────────────────

class TestCleanupTenant:
    def test_removes_entire_subtree(self, upload_root):
        layout = _seed_three_tenants(upload_root)
        # Sanity: 3 tenants × 2 notebooks × 2 files = 12 files total
        assert sum(1 for f in upload_root.rglob("*") if f.is_file()) == 12

        removed = upload_storage.cleanup_tenant("tenant-B")
        assert removed == 4  # 2 nb × 2 src
        assert not (upload_root / "tenant-B").exists()
        # Other tenants untouched.
        for f in layout["tenant-A"] + layout["tenant-C"]:
            assert f.exists()

    def test_idempotent_on_missing_tenant(self, upload_root):
        assert upload_storage.cleanup_tenant("never-existed") == 0

    def test_refuses_empty_tenant_id(self, upload_root):
        # Critical guard: empty string would resolve to the base itself
        # and wipe every tenant. Must refuse.
        upload_storage.save("safe", "nb1", "s", ".pdf", b"x")
        assert upload_storage.cleanup_tenant("") == 0
        assert (upload_root / "safe" / "nb1" / "s.pdf").exists()


# ── Read-only listing for ops ────────────────────────────────────────────────

class TestListTenantFiles:
    def test_returns_only_files_under_tenant(self, upload_root):
        upload_storage.save("t1", "nb1", "a", ".pdf", b"x")
        upload_storage.save("t1", "nb1", "b", ".docx", b"y")
        upload_storage.save("t2", "nb1", "c", ".pdf", b"z")
        files = list(upload_storage.list_tenant_files("t1"))
        assert len(files) == 2
        assert all("t1" in str(f.parent.parent) for f in files)

    def test_empty_for_unknown_tenant(self, upload_root):
        assert list(upload_storage.list_tenant_files("nope")) == []
