"""Unit tests for SPEC-INFRA-TENANT-DELETE-001 Phase 3 — deprovisioning step functions.

Each test class covers one step. All external calls (Docker, MongoDB, Redis, Qdrant,
httpx, boto3) are mocked so no real network or Docker daemon is required.

Pattern:
- `_make_state()` builds a minimal `_DeprovisionState`-like object (dataclass or
  SimpleNamespace) to avoid importing the orchestrator (avoids circular-import issues
  in tests before the orchestrator is written in Phase 4).
- Steps that call `transition_state` are patched at their import site.
- Redis steps use a real MagicMock for the sync redis.Redis client.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

# ---------------------------------------------------------------------------
# State stub — mirrors _DeprovisionState from deprovisioning_orchestrator.py
# (written in Phase 4). Tests import steps directly and pass a stub state.
# ---------------------------------------------------------------------------


def _make_state(**overrides) -> SimpleNamespace:
    """Return a minimal deprovisioning state stub."""
    defaults = {
        "db": AsyncMock(),
        "org_id": 42,
        "slug": "acme",
        "zitadel_org_id": "zitadel-org-abc",
        "zitadel_oidc_app_id": "zitadel-app-xyz",
        "litellm_team_id": "litellm-team-001",
        "moneybird_subscription_id": "mb-sub-1",
        "moneybird_contact_id": "mb-contact-2",
        "deprovisioner_user_id": "user-999",
        "deprovisioner_type": "owner",
        "org_name": "ACME Corp",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Step 0 — _mark_deprovisioning
# ---------------------------------------------------------------------------


class TestMarkDeprovisioning:
    @pytest.mark.asyncio
    async def test_calls_transition_state(self) -> None:
        """step 0 must call transition_state with 'deprovisioning' as to_state."""
        state = _make_state()
        with (
            patch(
                "app.services.provisioning.deprovisioning_steps.transition_state",
                new=AsyncMock(),
            ) as mock_transition,
            patch(
                "app.services.provisioning.deprovisioning_steps.invalidate_tenant_slug_cache",
                new=MagicMock(),
            ),
        ):
            from app.services.provisioning.deprovisioning_steps import _mark_deprovisioning

            await _mark_deprovisioning(state)
            mock_transition.assert_awaited_once()
            kwargs = mock_transition.call_args.kwargs
            assert kwargs["to_state"] == "deprovisioning"
            assert kwargs["step"] == "mark_deprovisioning"

    @pytest.mark.asyncio
    async def test_allows_deprovisioning_as_from_state(self) -> None:
        """Idempotent: from_state set must include 'deprovisioning' itself."""
        state = _make_state()
        with (
            patch(
                "app.services.provisioning.deprovisioning_steps.transition_state",
                new=AsyncMock(),
            ) as mock_transition,
            patch(
                "app.services.provisioning.deprovisioning_steps.invalidate_tenant_slug_cache",
                new=MagicMock(),
            ),
        ):
            from app.services.provisioning.deprovisioning_steps import _mark_deprovisioning

            await _mark_deprovisioning(state)
            from_state = mock_transition.call_args.kwargs["from_state"]
            assert "deprovisioning" in from_state

    @pytest.mark.asyncio
    async def test_invalidates_slug_cache(self) -> None:
        """Cache must be invalidated after the DB transition."""
        state = _make_state()
        with (
            patch(
                "app.services.provisioning.deprovisioning_steps.transition_state",
                new=AsyncMock(),
            ),
            patch(
                "app.services.provisioning.deprovisioning_steps.invalidate_tenant_slug_cache",
                new=MagicMock(),
            ) as mock_invalidate,
        ):
            from app.services.provisioning.deprovisioning_steps import _mark_deprovisioning

            await _mark_deprovisioning(state)
            mock_invalidate.assert_called_once()


# ---------------------------------------------------------------------------
# Step 1 — _delete_caddy_upstream
# ---------------------------------------------------------------------------


class TestDeleteCaddyUpstream:
    @pytest.mark.asyncio
    async def test_removes_caddyfile_and_reloads(self, tmp_path: Path) -> None:
        """Caddyfile is deleted and _reload_caddy is called via executor."""
        state = _make_state(slug="acme")
        tenant_file = tmp_path / "acme.caddyfile"
        tenant_file.write_text("# test")

        with (
            patch("app.services.provisioning.deprovisioning_steps.settings") as mock_settings,
            patch(
                "app.services.provisioning.deprovisioning_steps._reload_caddy",
                new=MagicMock(),
            ) as mock_reload,
            patch("app.services.provisioning.deprovisioning_steps._caddy_lock", new=asyncio.Lock()),
        ):
            mock_settings.caddy_tenants_path = str(tmp_path)
            from app.services.provisioning.deprovisioning_steps import _delete_caddy_upstream

            await _delete_caddy_upstream(state)

        assert not tenant_file.exists()
        mock_reload.assert_called_once()

    @pytest.mark.asyncio
    async def test_idempotent_missing_caddyfile(self, tmp_path: Path) -> None:
        """Missing caddyfile must not raise — idempotent."""
        state = _make_state(slug="acme")

        with (
            patch("app.services.provisioning.deprovisioning_steps.settings") as mock_settings,
            patch(
                "app.services.provisioning.deprovisioning_steps._reload_caddy",
                new=MagicMock(),
            ),
            patch("app.services.provisioning.deprovisioning_steps._caddy_lock", new=asyncio.Lock()),
        ):
            mock_settings.caddy_tenants_path = str(tmp_path)
            from app.services.provisioning.deprovisioning_steps import _delete_caddy_upstream

            await _delete_caddy_upstream(state)  # should not raise


# ---------------------------------------------------------------------------
# Step 2 — _delete_librechat_container
# ---------------------------------------------------------------------------


class TestDeleteLibrechatContainer:
    @pytest.mark.asyncio
    async def test_calls_sync_remove_container(self) -> None:
        """_sync_remove_container is called with the correct container name."""
        state = _make_state(slug="acme")

        with patch(
            "app.services.provisioning.deprovisioning_steps._sync_remove_container",
            new=MagicMock(),
        ) as mock_remove:
            from app.services.provisioning.deprovisioning_steps import _delete_librechat_container

            await _delete_librechat_container(state)

        mock_remove.assert_called_once_with("librechat-acme")


# ---------------------------------------------------------------------------
# Step 3 — _delete_librechat_filesystem
# ---------------------------------------------------------------------------


class TestDeleteLibrechatFilesystem:
    @pytest.mark.asyncio
    async def test_removes_tenant_directory(self, tmp_path: Path) -> None:
        """Tenant directory is removed if it exists."""
        state = _make_state(slug="acme")
        tenant_dir = tmp_path / "acme"
        tenant_dir.mkdir()
        (tenant_dir / "librechat.yaml").write_text("# test")

        with patch("app.services.provisioning.deprovisioning_steps.settings") as mock_settings:
            mock_settings.librechat_container_data_path = str(tmp_path)
            from app.services.provisioning.deprovisioning_steps import _delete_librechat_filesystem

            await _delete_librechat_filesystem(state)

        assert not tenant_dir.exists()

    @pytest.mark.asyncio
    async def test_idempotent_missing_directory(self, tmp_path: Path) -> None:
        """Non-existent directory must not raise."""
        state = _make_state(slug="acme")

        with patch("app.services.provisioning.deprovisioning_steps.settings") as mock_settings:
            mock_settings.librechat_container_data_path = str(tmp_path)
            from app.services.provisioning.deprovisioning_steps import _delete_librechat_filesystem

            await _delete_librechat_filesystem(state)  # should not raise


# ---------------------------------------------------------------------------
# Step 4 — _drop_mongodb_database
# ---------------------------------------------------------------------------


class TestDropMongodbDatabase:
    @pytest.mark.asyncio
    async def test_calls_sync_drop_database(self) -> None:
        """_sync_drop_mongodb_tenant_database is called with the slug."""
        state = _make_state(slug="acme")

        with patch(
            "app.services.provisioning.deprovisioning_steps._sync_drop_mongodb_tenant_database",
            new=MagicMock(),
        ) as mock_drop:
            from app.services.provisioning.deprovisioning_steps import _drop_mongodb_database

            await _drop_mongodb_database(state)

        mock_drop.assert_called_once_with("acme")


# ---------------------------------------------------------------------------
# Step 5 — _drop_mongodb_user
# ---------------------------------------------------------------------------


class TestDropMongodbUser:
    @pytest.mark.asyncio
    async def test_calls_sync_drop_user(self) -> None:
        """_sync_drop_mongodb_tenant_user is called with the slug."""
        state = _make_state(slug="acme")

        with patch(
            "app.services.provisioning.deprovisioning_steps._sync_drop_mongodb_tenant_user",
            new=MagicMock(),
        ) as mock_drop:
            from app.services.provisioning.deprovisioning_steps import _drop_mongodb_user

            await _drop_mongodb_user(state)

        mock_drop.assert_called_once_with("acme")


# ---------------------------------------------------------------------------
# Step 6 — _delete_meilisearch_index
# ---------------------------------------------------------------------------


class TestDeleteMeilisearchIndex:
    @pytest.mark.asyncio
    async def test_204_success(self) -> None:
        """204 No Content (or 200) means deletion succeeded — no exception raised."""
        state = _make_state(slug="acme")

        with patch("app.services.provisioning.deprovisioning_steps.settings") as mock_settings:
            mock_settings.meili_master_key = "test-key"
            with respx_router() as router:
                router.delete("/indexes/acme").mock(return_value=httpx.Response(200))
                from app.services.provisioning.deprovisioning_steps import _delete_meilisearch_index

                await _delete_meilisearch_index(state)

    @pytest.mark.asyncio
    async def test_404_is_idempotent(self) -> None:
        """404 = index already absent — must return without raising."""
        state = _make_state(slug="acme")

        with patch("app.services.provisioning.deprovisioning_steps.settings") as mock_settings:
            mock_settings.meili_master_key = "test-key"
            with respx_router() as router:
                router.delete("/indexes/acme").mock(return_value=httpx.Response(404))
                from app.services.provisioning.deprovisioning_steps import _delete_meilisearch_index

                await _delete_meilisearch_index(state)  # should not raise

    @pytest.mark.asyncio
    async def test_500_raises(self) -> None:
        """5xx from Meilisearch must propagate."""
        state = _make_state(slug="acme")

        with patch("app.services.provisioning.deprovisioning_steps.settings") as mock_settings:
            mock_settings.meili_master_key = "test-key"
            with respx_router() as router:
                router.delete("/indexes/acme").mock(return_value=httpx.Response(500))
                from app.services.provisioning.deprovisioning_steps import _delete_meilisearch_index

                with pytest.raises(httpx.HTTPStatusError):
                    await _delete_meilisearch_index(state)


# ---------------------------------------------------------------------------
# Step 7 — _flush_redis_tenant_keys
# ---------------------------------------------------------------------------


class TestFlushRedisTenantKeys:
    @pytest.mark.asyncio
    async def test_deletes_matching_keys(self) -> None:
        """Keys matching configs:{slug}:* must be UNLINKed."""
        state = _make_state(slug="acme")

        mock_redis = MagicMock()
        mock_redis.__enter__ = MagicMock(return_value=mock_redis)
        mock_redis.__exit__ = MagicMock(return_value=False)
        mock_redis.scan_iter.return_value = iter(["configs:acme:foo", "configs:acme:bar"])
        mock_redis.unlink.return_value = 2
        mock_redis.close = MagicMock()

        with (
            patch("app.services.provisioning.deprovisioning_steps.settings") as mock_settings,
            patch("redis.Redis", return_value=mock_redis),
        ):
            mock_settings.redis_host = "localhost"
            mock_settings.redis_port = 6379
            mock_settings.redis_password = None
            from app.services.provisioning.deprovisioning_steps import _flush_redis_tenant_keys

            await _flush_redis_tenant_keys(state)

        mock_redis.unlink.assert_called()

    @pytest.mark.asyncio
    async def test_no_keys_no_unlink(self) -> None:
        """When no keys match, UNLINK must not be called."""
        state = _make_state(slug="acme")

        mock_redis = MagicMock()
        mock_redis.__enter__ = MagicMock(return_value=mock_redis)
        mock_redis.__exit__ = MagicMock(return_value=False)
        mock_redis.scan_iter.return_value = iter([])
        mock_redis.close = MagicMock()

        with (
            patch("app.services.provisioning.deprovisioning_steps.settings") as mock_settings,
            patch("redis.Redis", return_value=mock_redis),
        ):
            mock_settings.redis_host = "localhost"
            mock_settings.redis_port = 6379
            mock_settings.redis_password = None
            from app.services.provisioning.deprovisioning_steps import _flush_redis_tenant_keys

            await _flush_redis_tenant_keys(state)

        mock_redis.unlink.assert_not_called()


# ---------------------------------------------------------------------------
# Step 8 — _delete_qdrant_points
# ---------------------------------------------------------------------------


class TestDeleteQdrantPoints:
    @pytest.mark.asyncio
    async def test_deletes_from_both_collections(self) -> None:
        """Must call delete on klai_knowledge and klai_focus."""
        state = _make_state(org_id=42, slug="acme")
        mock_client = AsyncMock()
        mock_client.close = AsyncMock()

        with (
            patch("app.services.provisioning.deprovisioning_steps.settings") as mock_settings,
            patch("qdrant_client.AsyncQdrantClient", return_value=mock_client),
        ):
            mock_settings.qdrant_url = "http://qdrant:6333"
            mock_settings.qdrant_api_key = ""
            from app.services.provisioning.deprovisioning_steps import _delete_qdrant_points

            await _delete_qdrant_points(state)

        assert mock_client.delete.await_count == 2
        call_args = [call.kwargs.get("collection_name") or call.args[0] for call in mock_client.delete.await_args_list]
        assert "klai_knowledge" in call_args
        assert "klai_focus" in call_args

    @pytest.mark.asyncio
    async def test_collection_not_found_is_idempotent(self) -> None:
        """404-like exception from Qdrant must not propagate."""
        state = _make_state(org_id=42, slug="acme")
        mock_client = AsyncMock()
        mock_client.delete.side_effect = Exception("Not found: collection does not exist")
        mock_client.close = AsyncMock()

        with (
            patch("app.services.provisioning.deprovisioning_steps.settings") as mock_settings,
            patch("qdrant_client.AsyncQdrantClient", return_value=mock_client),
        ):
            mock_settings.qdrant_url = "http://qdrant:6333"
            mock_settings.qdrant_api_key = ""
            from app.services.provisioning.deprovisioning_steps import _delete_qdrant_points

            await _delete_qdrant_points(state)  # should not raise


# ---------------------------------------------------------------------------
# Step 9 — _delete_falkordb_graph
# ---------------------------------------------------------------------------


class TestDeleteFalkordbGraph:
    @pytest.mark.asyncio
    async def test_200_ok(self) -> None:
        """200 response means graph was wiped."""
        state = _make_state(org_id=42, slug="acme")

        with patch("app.services.provisioning.deprovisioning_steps.settings") as mock_settings:
            mock_settings.knowledge_ingest_url = "http://knowledge-ingest:8000"
            mock_settings.knowledge_ingest_secret = "secret"
            with patch("app.services.provisioning.deprovisioning_steps.get_trace_headers", return_value={}):
                with respx_router(base_url="http://knowledge-ingest:8000") as router:
                    router.post("/internal/v1/orgs/42/wipe-graph").mock(
                        return_value=httpx.Response(200, json={"nodes_deleted": 5, "status": "ok"})
                    )
                    from app.services.provisioning.deprovisioning_steps import _delete_falkordb_graph

                    await _delete_falkordb_graph(state)

    @pytest.mark.asyncio
    async def test_404_is_idempotent(self) -> None:
        """404 means graph already absent — no exception."""
        state = _make_state(org_id=42, slug="acme")

        with patch("app.services.provisioning.deprovisioning_steps.settings") as mock_settings:
            mock_settings.knowledge_ingest_url = "http://knowledge-ingest:8000"
            mock_settings.knowledge_ingest_secret = "secret"
            with patch("app.services.provisioning.deprovisioning_steps.get_trace_headers", return_value={}):
                with respx_router(base_url="http://knowledge-ingest:8000") as router:
                    router.post("/internal/v1/orgs/42/wipe-graph").mock(return_value=httpx.Response(404))
                    from app.services.provisioning.deprovisioning_steps import _delete_falkordb_graph

                    await _delete_falkordb_graph(state)

    @pytest.mark.asyncio
    async def test_skips_when_no_url(self) -> None:
        """Empty knowledge_ingest_url means step is skipped gracefully."""
        state = _make_state(org_id=42, slug="acme")

        with patch("app.services.provisioning.deprovisioning_steps.settings") as mock_settings:
            mock_settings.knowledge_ingest_url = ""
            from app.services.provisioning.deprovisioning_steps import _delete_falkordb_graph

            await _delete_falkordb_graph(state)  # must not raise or make network calls


# ---------------------------------------------------------------------------
# Step 10 — _delete_scribe_artifacts
# ---------------------------------------------------------------------------


class TestDeleteScribeArtifacts:
    @pytest.mark.asyncio
    async def test_skips_when_no_s3_endpoint(self) -> None:
        """Empty garage_s3_endpoint disables the step (feature-flag)."""
        state = _make_state(slug="acme")

        with patch("app.services.provisioning.deprovisioning_steps.settings") as mock_settings:
            mock_settings.garage_s3_endpoint = ""
            from app.services.provisioning.deprovisioning_steps import _delete_scribe_artifacts

            await _delete_scribe_artifacts(state)  # must not raise

    @pytest.mark.asyncio
    async def test_deletes_s3_objects(self) -> None:
        """When S3 is configured, all objects under {slug}/ prefix are deleted."""
        state = _make_state(slug="acme")

        mock_s3 = MagicMock()
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [
            {"Contents": [{"Key": "acme/file1.txt"}, {"Key": "acme/file2.mp3"}]},
        ]
        mock_s3.get_paginator.return_value = mock_paginator
        mock_s3.delete_objects = MagicMock()

        with (
            patch("app.services.provisioning.deprovisioning_steps.settings") as mock_settings,
            patch("boto3.client", return_value=mock_s3),
        ):
            mock_settings.garage_s3_endpoint = "http://garage:3900"
            mock_settings.garage_s3_access_key = "key"
            mock_settings.garage_s3_secret_key = "secret"
            mock_settings.garage_s3_bucket = "klai-scribe"
            from app.services.provisioning.deprovisioning_steps import _delete_scribe_artifacts

            await _delete_scribe_artifacts(state)

        mock_s3.delete_objects.assert_called_once()
        delete_payload = mock_s3.delete_objects.call_args.kwargs["Delete"]
        keys = [obj["Key"] for obj in delete_payload["Objects"]]
        assert "acme/file1.txt" in keys
        assert "acme/file2.mp3" in keys


# ---------------------------------------------------------------------------
# Step 11 — _delete_litellm_team
# ---------------------------------------------------------------------------


class TestDeleteLitellmTeam:
    @pytest.mark.asyncio
    async def test_posts_team_delete(self) -> None:
        """POST /team/delete is called with the team_id."""
        state = _make_state(slug="acme", litellm_team_id="team-abc")

        with patch("app.services.provisioning.deprovisioning_steps.settings") as mock_settings:
            mock_settings.litellm_base_url = "http://litellm:4000"
            mock_settings.litellm_master_key = "master"
            with respx_router(base_url="http://litellm:4000") as router:
                router.post("/team/delete").mock(return_value=httpx.Response(200))
                from app.services.provisioning.deprovisioning_steps import _delete_litellm_team

                await _delete_litellm_team(state)

    @pytest.mark.asyncio
    async def test_404_is_idempotent(self) -> None:
        """404 means team already gone — no exception."""
        state = _make_state(slug="acme", litellm_team_id="team-abc")

        with patch("app.services.provisioning.deprovisioning_steps.settings") as mock_settings:
            mock_settings.litellm_base_url = "http://litellm:4000"
            mock_settings.litellm_master_key = "master"
            with respx_router(base_url="http://litellm:4000") as router:
                router.post("/team/delete").mock(return_value=httpx.Response(404))
                from app.services.provisioning.deprovisioning_steps import _delete_litellm_team

                await _delete_litellm_team(state)

    @pytest.mark.asyncio
    async def test_skips_when_no_team_id(self) -> None:
        """Empty litellm_team_id skips the step gracefully."""
        state = _make_state(slug="acme", litellm_team_id="")

        with patch("app.services.provisioning.deprovisioning_steps.settings") as mock_settings:
            mock_settings.litellm_base_url = "http://litellm:4000"
            mock_settings.litellm_master_key = "master"
            from app.services.provisioning.deprovisioning_steps import _delete_litellm_team

            await _delete_litellm_team(state)  # must not raise or make network calls


# ---------------------------------------------------------------------------
# Step 12 — _archive_moneybird_subscription
# ---------------------------------------------------------------------------


class TestArchiveMoneybirdSubscription:
    @pytest.mark.asyncio
    async def test_calls_stop_and_archive(self) -> None:
        """Both stop_subscription and archive_contact are called."""
        state = _make_state(moneybird_subscription_id="sub-1", moneybird_contact_id="con-2")

        mock_client = AsyncMock()
        mock_client.stop_subscription = AsyncMock()
        mock_client.archive_contact = AsyncMock()
        mock_client.close = AsyncMock()

        # Lazy import inside the function — patch the source module
        with (
            patch("app.services.provisioning.deprovisioning_steps.settings") as mock_settings,
            patch(
                "app.services.moneybird_client.get_moneybird_client",
                return_value=mock_client,
            ),
        ):
            mock_settings.moneybird_api_token = "token"
            from app.services.provisioning.deprovisioning_steps import _archive_moneybird_subscription

            await _archive_moneybird_subscription(state)

        mock_client.stop_subscription.assert_awaited_once_with("sub-1")
        mock_client.archive_contact.assert_awaited_once_with("con-2")

    @pytest.mark.asyncio
    async def test_skips_when_no_moneybird_ids(self) -> None:
        """Missing subscription_id and contact_id skips the step."""
        state = _make_state(moneybird_subscription_id=None, moneybird_contact_id=None)

        with patch("app.services.provisioning.deprovisioning_steps.settings") as mock_settings:
            mock_settings.moneybird_api_token = "token"
            from app.services.provisioning.deprovisioning_steps import _archive_moneybird_subscription

            await _archive_moneybird_subscription(state)  # must not raise

    @pytest.mark.asyncio
    async def test_skips_when_no_api_token(self) -> None:
        """Empty moneybird_api_token skips the step with a warning."""
        state = _make_state(moneybird_subscription_id="sub-1", moneybird_contact_id=None)

        with patch("app.services.provisioning.deprovisioning_steps.settings") as mock_settings:
            mock_settings.moneybird_api_token = ""
            from app.services.provisioning.deprovisioning_steps import _archive_moneybird_subscription

            await _archive_moneybird_subscription(state)  # must not raise


# ---------------------------------------------------------------------------
# Step 13 — _delete_personal_kb
# ---------------------------------------------------------------------------


class TestDeletePersonalKb:
    @pytest.mark.asyncio
    async def test_calls_deprovision_kb(self) -> None:
        """docs_api.deprovision_kb is called with the correct slug."""
        state = _make_state(slug="acme")

        # The step does `from app.services import docs_client as docs_api` lazily
        with patch(
            "app.services.docs_client.deprovision_kb",
            new=AsyncMock(),
        ) as mock_deprovision_kb:
            from app.services.provisioning.deprovisioning_steps import _delete_personal_kb

            await _delete_personal_kb(state)

        mock_deprovision_kb.assert_awaited_once_with(org_slug="acme", kb_slug="personal")

    @pytest.mark.asyncio
    async def test_404_is_idempotent(self) -> None:
        """HTTPStatusError with 404 must be swallowed."""
        state = _make_state(slug="acme")

        request = httpx.Request("DELETE", "http://docs-app/kb/acme/personal")
        response = httpx.Response(404, request=request)
        error = httpx.HTTPStatusError("not found", request=request, response=response)

        with patch(
            "app.services.docs_client.deprovision_kb",
            new=AsyncMock(side_effect=error),
        ):
            from app.services.provisioning.deprovisioning_steps import _delete_personal_kb

            await _delete_personal_kb(state)  # must not raise


# ---------------------------------------------------------------------------
# Step 14 — _delete_zitadel_oidc_app
# ---------------------------------------------------------------------------


class TestDeleteZitadelOidcApp:
    @pytest.mark.asyncio
    async def test_calls_delete_librechat_oidc_app(self) -> None:
        """zitadel.delete_librechat_oidc_app is called with app_id."""
        state = _make_state(zitadel_oidc_app_id="app-abc")

        # Lazy import inside function: `from app.services.zitadel import zitadel`
        with patch(
            "app.services.zitadel.zitadel.delete_librechat_oidc_app",
            new=AsyncMock(),
        ) as mock_delete:
            from app.services.provisioning.deprovisioning_steps import _delete_zitadel_oidc_app

            await _delete_zitadel_oidc_app(state)

        mock_delete.assert_awaited_once_with("app-abc")

    @pytest.mark.asyncio
    async def test_skips_when_no_app_id(self) -> None:
        """Missing app_id skips the step without calling zitadel."""
        state = _make_state(zitadel_oidc_app_id="")

        with patch(
            "app.services.zitadel.zitadel.delete_librechat_oidc_app",
            new=AsyncMock(),
        ) as mock_delete:
            from app.services.provisioning.deprovisioning_steps import _delete_zitadel_oidc_app

            await _delete_zitadel_oidc_app(state)

        mock_delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# Step 15 — _delete_zitadel_org
# ---------------------------------------------------------------------------


class TestDeleteZitadelOrg:
    @pytest.mark.asyncio
    async def test_calls_delete_org(self) -> None:
        """zitadel.delete_org is called with the zitadel_org_id."""
        state = _make_state(zitadel_org_id="zit-org-1")

        with patch(
            "app.services.zitadel.zitadel.delete_org",
            new=AsyncMock(),
        ) as mock_delete:
            from app.services.provisioning.deprovisioning_steps import _delete_zitadel_org

            await _delete_zitadel_org(state)

        mock_delete.assert_awaited_once_with("zit-org-1")


# ---------------------------------------------------------------------------
# Step 16 — _finalize_postgres_delete
# ---------------------------------------------------------------------------


class TestFinalizePostgresDelete:
    @pytest.mark.asyncio
    async def test_emits_lifecycle_event_and_commits(self) -> None:
        """Lifecycle event is emitted and db.commit is called."""
        state = _make_state(org_id=42, slug="acme", org_name="ACME Corp")

        # Lazy import: `from app.services.audit.tenant_lifecycle import emit_lifecycle_event`
        with patch(
            "app.services.audit.tenant_lifecycle.emit_lifecycle_event",
            new=AsyncMock(),
        ) as mock_emit:
            from app.services.provisioning.deprovisioning_steps import _finalize_postgres_delete

            await _finalize_postgres_delete(state)

        mock_emit.assert_awaited_once()
        state.db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_called_for_each_table(self) -> None:
        """db.execute is called at least 5 times (4 child DELETEs + 1 portal_orgs DELETE)."""
        state = _make_state(org_id=42, slug="acme")

        with patch(
            "app.services.audit.tenant_lifecycle.emit_lifecycle_event",
            new=AsyncMock(),
        ):
            from app.services.provisioning.deprovisioning_steps import _finalize_postgres_delete

            await _finalize_postgres_delete(state)

        assert state.db.execute.await_count >= 5


# ---------------------------------------------------------------------------
# STEPS list
# ---------------------------------------------------------------------------


class TestStepsList:
    def test_has_17_entries(self) -> None:
        """STEPS must contain exactly 17 entries (step 0..16)."""
        from app.services.provisioning.deprovisioning_steps import STEPS

        assert len(STEPS) == 17

    def test_all_entries_are_callables(self) -> None:
        """Every entry in STEPS must be an async callable."""
        import asyncio

        from app.services.provisioning.deprovisioning_steps import STEPS

        for step in STEPS:
            assert callable(step)
            # Each step should be a coroutine function
            assert asyncio.iscoroutinefunction(step), f"{step.__name__} is not a coroutine function"


# ---------------------------------------------------------------------------
# Helpers — respx context manager shortcut
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def respx_router(base_url: str = "http://meilisearch:7700"):
    with respx.mock(base_url=base_url, assert_all_called=False) as router:
        yield router
