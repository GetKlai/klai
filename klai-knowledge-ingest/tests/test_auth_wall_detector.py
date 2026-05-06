"""SPEC-INGEST-LOGIN-WALL-DETECT-002 Phase B.2 -- detector cluster tests.

Replaces v1's phrase-substring tests with v2's cluster-detection tests. The
detector flags a page as a wall iff N or more OTHER pages in the same
``(org_id, kb_slug)`` have a SimHash within Hamming distance 3 of the page's
own SimHash. ``N`` defaults to 5 per REQ-02; the Hamming threshold is fixed.

The detector is async because cluster lookup queries the database. Tests
inject a stub ``_FakeConn`` instead of asyncpg so the unit tests stay
hermetic; pytest-asyncio auto-mode (set in ``pyproject.toml``) handles the
event loop.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knowledge_ingest.utils.auth_wall_detector import (
    AuthWallSignal,
    detect_anonymous_auth_wall,
)
from knowledge_ingest.utils.content_fingerprint import (
    compute_simhash,
    hamming_distance,
)

FIXTURES = Path(__file__).parent / "fixtures"
WALLS = FIXTURES / "auth_walls"
CLEAN = FIXTURES / "clean_pages"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Fake asyncpg connection
# ---------------------------------------------------------------------------


class _FakeConn:
    """Stub for ``asyncpg.Connection`` exposing only ``fetch``.

    Records every fetch invocation (query string + positional args) so tests
    can assert on tenant scoping and self-exclusion in the SQL.
    """

    def __init__(self, simhashes: list[int | None]) -> None:
        self._simhashes: list[int | None] = simhashes
        self.fetch_calls: list[tuple[str, tuple]] = []

    async def fetch(self, query: str, *args):
        self.fetch_calls.append((query, args))
        return [{"content_simhash": h} for h in self._simhashes]


def _hash_with_low_bits_flipped(base: int, flip_bits: int) -> int:
    """Return ``base`` with the lowest ``flip_bits`` bits flipped.

    Hamming distance from ``base`` is exactly ``flip_bits`` — useful for
    testing boundary conditions on the Hamming threshold without hand-rolling
    SimHash inputs.
    """
    base_u64 = base & 0xFFFFFFFFFFFFFFFF
    return base_u64 ^ ((1 << flip_bits) - 1)


# ---------------------------------------------------------------------------
# REQ-02 cluster-based detection
# ---------------------------------------------------------------------------


class TestClusterDetection:
    """Cluster size >= cluster_min OTHER pages → flag (AC-02.1, AC-02.4)."""

    @pytest.mark.asyncio
    async def test_flags_when_5_others_match_exactly(self) -> None:
        wall_text = _read(WALLS / "redcactus_hubspot.md")
        target = compute_simhash(wall_text)
        siblings = [target] * 5  # 5 OTHERS, all at distance 0
        conn = _FakeConn(siblings)

        signal = await detect_anonymous_auth_wall(
            wall_text,
            org_id="org-1",
            kb_slug="kb-1",
            url="https://x/wall",
            conn=conn,
        )
        assert signal is not None
        assert signal.pattern == "template_cluster"
        assert signal.evidence == ("cluster_size=5 hamming<=3",)
        assert signal.confidence == 0.9

    @pytest.mark.asyncio
    async def test_no_flag_when_only_4_others_match(self) -> None:
        """AC-02.4: cluster boundary at strict-N OTHERS (4 < 5 → no cluster)."""
        wall_text = _read(WALLS / "redcactus_hubspot.md")
        target = compute_simhash(wall_text)
        siblings = [target] * 4
        conn = _FakeConn(siblings)

        signal = await detect_anonymous_auth_wall(
            wall_text,
            org_id="org-1",
            kb_slug="kb-1",
            url="https://x/wall",
            conn=conn,
        )
        assert signal is None

    @pytest.mark.asyncio
    async def test_hamming_4_not_counted_strict_threshold(self) -> None:
        """AC-02.5: Hamming distance == 4 is OUTSIDE the cluster boundary."""
        wall_text = _read(WALLS / "redcactus_hubspot.md")
        target = compute_simhash(wall_text)
        far_sibling = _hash_with_low_bits_flipped(target, 4)
        # Sanity: the helper produced exactly Hamming 4.
        assert hamming_distance(target, far_sibling) == 4

        conn = _FakeConn([far_sibling] * 5)
        signal = await detect_anonymous_auth_wall(
            wall_text,
            org_id="org-1",
            kb_slug="kb-1",
            url="https://x/wall",
            conn=conn,
        )
        assert signal is None

    @pytest.mark.asyncio
    async def test_hamming_3_counted_at_threshold(self) -> None:
        """The boundary is inclusive: Hamming 3 IS in the cluster."""
        wall_text = _read(WALLS / "redcactus_hubspot.md")
        target = compute_simhash(wall_text)
        boundary_sibling = _hash_with_low_bits_flipped(target, 3)
        assert hamming_distance(target, boundary_sibling) == 3

        conn = _FakeConn([boundary_sibling] * 5)
        signal = await detect_anonymous_auth_wall(
            wall_text,
            org_id="org-1",
            kb_slug="kb-1",
            url="https://x/wall",
            conn=conn,
        )
        assert signal is not None
        assert "cluster_size=5" in signal.evidence[0]

    @pytest.mark.asyncio
    async def test_148_redcactus_walls_form_cluster(self) -> None:
        """AC-02.1: 149-page production cluster yields cluster_size=148.

        Production scenario: voys/support has 149 RedCactus walls. When we
        evaluate any one wall, the cluster query returns the OTHER 148 (URL
        exclusion drops the page's own row).
        """
        wall_text = _read(WALLS / "redcactus_hubspot.md")
        target = compute_simhash(wall_text)
        conn = _FakeConn([target] * 148)

        signal = await detect_anonymous_auth_wall(
            wall_text,
            org_id="org-voys",
            kb_slug="support",
            url="https://wiki.redcactus.cloud/nl/crm-software/HubSpot",
            conn=conn,
        )
        assert signal is not None
        assert signal.pattern == "template_cluster"
        assert "cluster_size=148" in signal.evidence[0]


# ---------------------------------------------------------------------------
# REQ-03 cold-start permissiveness
# ---------------------------------------------------------------------------


class TestColdStart:
    """Pages in tenants with too few siblings return None (AC-03)."""

    @pytest.mark.asyncio
    async def test_zero_siblings_returns_none(self) -> None:
        """AC-03.1: brand-new tenant, 1 page total → None."""
        wall_text = _read(WALLS / "redcactus_hubspot.md")
        conn = _FakeConn([])  # no other pages in this KB yet

        signal = await detect_anonymous_auth_wall(
            wall_text,
            org_id="new-tenant",
            kb_slug="support",
            url="https://x/page-1",
            conn=conn,
        )
        assert signal is None

    @pytest.mark.asyncio
    async def test_below_threshold_siblings_returns_none(self) -> None:
        """AC-03.3: 4 OTHERS clustering → still below default 5 → None."""
        wall_text = _read(WALLS / "redcactus_hubspot.md")
        target = compute_simhash(wall_text)
        conn = _FakeConn([target] * 4)

        signal = await detect_anonymous_auth_wall(
            wall_text,
            org_id="small-tenant",
            kb_slug="kb-1",
            url="https://x/page-1",
            conn=conn,
        )
        assert signal is None

    @pytest.mark.asyncio
    async def test_null_simhash_rows_skipped(self) -> None:
        """Sibling rows with NULL content_simhash (legacy, pre-backfill) are skipped."""
        wall_text = _read(WALLS / "redcactus_hubspot.md")
        target = compute_simhash(wall_text)
        # 4 NULL siblings + 5 real → 5 cluster members, NULLs ignored
        siblings: list[int | None] = [None] * 4 + [target] * 5
        conn = _FakeConn(siblings)

        signal = await detect_anonymous_auth_wall(
            wall_text,
            org_id="x",
            kb_slug="y",
            url="https://x/p",
            conn=conn,
        )
        assert signal is not None
        assert "cluster_size=5" in signal.evidence[0]


# ---------------------------------------------------------------------------
# REQ-02.2 production FPs do NOT cluster against RedCactus walls
# ---------------------------------------------------------------------------


class TestProductionFalsePositives:
    """The 5 captured production FPs must NOT cluster with RedCactus walls.

    Production scenario: voys/support has 148 RedCactus walls plus a handful
    of legitimate tutorials. When any FP page is evaluated against the wall
    cluster, its content fingerprint differs enough that 0 wall-siblings fall
    within Hamming 3 — so the FP returns None.
    """

    FP_FIXTURES = (
        "voys_account_toegang.md",
        "redcactus_ifttt.md",
        "redcactus_zoom_setup_nl.md",
        "auth_documentation_tutorial.md",
        "de_only_login.md",
    )

    @pytest.mark.parametrize("fp_name", FP_FIXTURES)
    @pytest.mark.asyncio
    async def test_fp_not_in_redcactus_wall_cluster(self, fp_name: str) -> None:
        fp_text = _read(CLEAN / fp_name)
        wall_text = _read(WALLS / "redcactus_hubspot.md")
        wall_hash = compute_simhash(wall_text)
        wall_cluster = [wall_hash] * 148  # production-scale wall cluster

        conn = _FakeConn(wall_cluster)
        signal = await detect_anonymous_auth_wall(
            fp_text,
            org_id="org-voys",
            kb_slug="support",
            url=f"https://x/{fp_name}",
            conn=conn,
        )
        assert signal is None, (
            f"{fp_name} clustered with the RedCactus wall cluster — FP regression"
        )


# ---------------------------------------------------------------------------
# REQ-06 caller signature stability + fail-open
# ---------------------------------------------------------------------------


class TestSignatureStability:
    """v1 callers (no DB args) must still work — they get None (AC-06)."""

    @pytest.mark.asyncio
    async def test_no_db_args_returns_none(self) -> None:
        """AC-06.2: missing org_id/kb_slug/conn → fail-open None."""
        signal = await detect_anonymous_auth_wall(
            "any text", fit_markdown="more text", url="https://x"
        )
        assert signal is None

    @pytest.mark.asyncio
    async def test_partial_db_args_returns_none(self) -> None:
        """org_id without conn → still fail-open."""
        signal = await detect_anonymous_auth_wall(
            "any text", org_id="x", kb_slug="y", url="https://x"
        )
        assert signal is None

    @pytest.mark.asyncio
    async def test_empty_input_returns_none(self) -> None:
        conn = _FakeConn([])
        signal = await detect_anonymous_auth_wall(
            "", org_id="x", kb_slug="y", url="https://x", conn=conn
        )
        assert signal is None


# ---------------------------------------------------------------------------
# REQ-09 tenant isolation in the SQL
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    """SQL must filter by both org_id AND kb_slug (AC-09.1)."""

    @pytest.mark.asyncio
    async def test_query_filters_by_org_and_kb(self) -> None:
        wall_text = _read(WALLS / "redcactus_hubspot.md")
        conn = _FakeConn([])

        await detect_anonymous_auth_wall(
            wall_text,
            org_id="org-voys",
            kb_slug="support",
            url="https://x/p",
            conn=conn,
        )
        assert len(conn.fetch_calls) == 1
        query, args = conn.fetch_calls[0]
        assert "org_id" in query, "SQL must filter by org_id"
        assert "kb_slug" in query, "SQL must filter by kb_slug"
        assert "org-voys" in args
        assert "support" in args

    @pytest.mark.asyncio
    async def test_query_excludes_self_via_url(self) -> None:
        """Backfill path: target page is in DB → must NOT count itself."""
        wall_text = _read(WALLS / "redcactus_hubspot.md")
        conn = _FakeConn([])
        page_url = "https://x/the-page"

        await detect_anonymous_auth_wall(
            wall_text,
            org_id="org-1",
            kb_slug="kb-1",
            url=page_url,
            conn=conn,
        )
        query, args = conn.fetch_calls[0]
        # Either explicit URL exclusion clause is acceptable — Postgres syntax
        # allows both `<>` and `!=`.
        lower = query.lower()
        assert "url <>" in lower or "url !=" in lower, (
            "URL exclusion clause missing — backfill would count target page itself"
        )
        assert page_url in args


# ---------------------------------------------------------------------------
# REQ-07 signal shape (constant pattern + evidence shape)
# ---------------------------------------------------------------------------


class TestSignalShape:
    """The AuthWallSignal returned matches v2 contract (REQ-07)."""

    @pytest.mark.asyncio
    async def test_signal_pattern_is_template_cluster(self) -> None:
        wall_text = _read(WALLS / "redcactus_hubspot.md")
        target = compute_simhash(wall_text)
        conn = _FakeConn([target] * 5)

        signal = await detect_anonymous_auth_wall(
            wall_text,
            org_id="x",
            kb_slug="y",
            url="https://x/p",
            conn=conn,
        )
        assert signal is not None
        assert signal.pattern == "template_cluster"
        assert signal.confidence == 0.9
        assert len(signal.evidence) == 1
        assert "cluster_size=" in signal.evidence[0]
        assert "hamming<=3" in signal.evidence[0]

    def test_authwall_signal_dataclass_fields(self) -> None:
        """The dataclass remains usable from sync code (e.g., the exception)."""
        signal = AuthWallSignal(
            pattern="template_cluster",
            evidence=("cluster_size=10 hamming<=3",),
            confidence=0.9,
        )
        assert signal.pattern == "template_cluster"
        assert signal.evidence == ("cluster_size=10 hamming<=3",)
        assert signal.confidence == 0.9


# ---------------------------------------------------------------------------
# REQ-02.3 synthetic CMS fixtures
# ---------------------------------------------------------------------------


class TestSyntheticCmsClusters:
    """Synthetic Confluence/WordPress/Notion fixtures cluster (AC-02.3)."""

    @pytest.mark.parametrize(
        "wall_name",
        [
            "confluence_login_required.md",
            "wordpress_login_redirect.md",
            "notion_private_page.md",
        ],
    )
    @pytest.mark.asyncio
    async def test_synthetic_cms_walls_cluster(self, wall_name: str) -> None:
        wall_text = _read(WALLS / wall_name)
        target = compute_simhash(wall_text)
        conn = _FakeConn([target] * 5)

        signal = await detect_anonymous_auth_wall(
            wall_text,
            org_id="x",
            kb_slug="y",
            url=f"https://x/{wall_name}",
            conn=conn,
        )
        assert signal is not None
        assert signal.pattern == "template_cluster"


# ---------------------------------------------------------------------------
# Configurable cluster_min argument
# ---------------------------------------------------------------------------


class TestConfigurableThreshold:
    """``cluster_min`` arg overrides the default 5."""

    @pytest.mark.asyncio
    async def test_lower_threshold_flags_smaller_cluster(self) -> None:
        wall_text = _read(WALLS / "redcactus_hubspot.md")
        target = compute_simhash(wall_text)
        conn = _FakeConn([target] * 3)  # 3 OTHERS

        # Default 5 → no flag
        assert await detect_anonymous_auth_wall(
            wall_text, org_id="x", kb_slug="y", url="https://x", conn=conn
        ) is None
        # Lowered to 3 → flag
        signal = await detect_anonymous_auth_wall(
            wall_text,
            org_id="x",
            kb_slug="y",
            url="https://x",
            conn=conn,
            cluster_min=3,
        )
        assert signal is not None
        assert "cluster_size=3" in signal.evidence[0]
