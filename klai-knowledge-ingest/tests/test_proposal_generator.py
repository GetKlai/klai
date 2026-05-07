"""Tests for proposal_generator — batch unmatched document proposal logic."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.proposal_generator import DocumentSummary, maybe_generate_proposal
from knowledge_ingest.taxonomy_classifier import TaxonomyNode


def _make_docs(count: int) -> list[DocumentSummary]:
    return [DocumentSummary(title=f"Doc {i}", content_preview=f"Content {i}") for i in range(count)]


def _make_nodes(*names: str) -> list[TaxonomyNode]:
    return [TaxonomyNode(id=i + 1, name=name) for i, name in enumerate(names)]


def _mock_litellm_category(name: str) -> AsyncMock:
    response_json = {"choices": [{"message": {"content": json.dumps({"category_name": name})}}]}
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=response_json)

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_resp)
    return mock_client


class TestMaybeGenerateProposal:
    @pytest.mark.asyncio
    async def test_no_proposal_below_threshold(self):
        """Less than 3 unmatched documents → no proposal submitted."""
        docs = _make_docs(2)
        nodes = _make_nodes("Billing")

        with patch("knowledge_ingest.proposal_generator.submit_taxonomy_proposal") as mock_submit:
            with patch("knowledge_ingest.proposal_generator.settings") as mock_settings:
                mock_settings.portal_internal_token = "secret"
                mock_settings.taxonomy_classification_timeout = 5.0
                mock_settings.litellm_url = "http://litellm:4000"
                mock_settings.litellm_api_key = "key"
                mock_settings.taxonomy_classification_model = "klai-fast"
                await maybe_generate_proposal("org1", "kb1", docs, nodes)
            mock_submit.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_proposal_when_no_token(self):
        """Missing PORTAL_INTERNAL_TOKEN → skips silently."""
        docs = _make_docs(5)
        nodes = _make_nodes("Billing")

        with patch("knowledge_ingest.proposal_generator.submit_taxonomy_proposal") as mock_submit:
            with patch("knowledge_ingest.proposal_generator.settings") as mock_settings:
                mock_settings.portal_internal_token = ""
                await maybe_generate_proposal("org1", "kb1", docs, nodes)
            mock_submit.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_proposal_when_name_already_exists(self):
        """Suggested name matches existing node → no duplicate submitted."""
        docs = _make_docs(5)
        nodes = _make_nodes("API Documentation", "Billing")

        mock_client = _mock_litellm_category("API Documentation")

        with (
            patch(
                "knowledge_ingest.proposal_generator.httpx.AsyncClient", return_value=mock_client
            ),
            patch("knowledge_ingest.proposal_generator.submit_taxonomy_proposal") as mock_submit,
            patch("knowledge_ingest.proposal_generator.settings") as mock_settings,
        ):
            mock_settings.portal_internal_token = "secret"
            mock_settings.taxonomy_classification_timeout = 5.0
            mock_settings.litellm_url = "http://litellm:4000"
            mock_settings.litellm_api_key = "key"
            mock_settings.taxonomy_classification_model = "klai-fast"
            await maybe_generate_proposal("org1", "kb1", docs, nodes)

        mock_submit.assert_not_called()

    @pytest.mark.asyncio
    async def test_submits_proposal_when_threshold_met(self):
        """3+ unmatched docs + new name → proposal submitted."""
        docs = _make_docs(4)
        nodes = _make_nodes("Billing")

        mock_client = _mock_litellm_category("Developer Resources")

        with (
            patch(
                "knowledge_ingest.proposal_generator.httpx.AsyncClient", return_value=mock_client
            ),
            patch(
                "knowledge_ingest.proposal_generator.submit_taxonomy_proposal",
                new_callable=AsyncMock,
            ) as mock_submit,
            patch("knowledge_ingest.proposal_generator.settings") as mock_settings,
        ):
            mock_settings.portal_internal_token = "secret"
            mock_settings.taxonomy_classification_timeout = 5.0
            mock_settings.litellm_url = "http://litellm:4000"
            mock_settings.litellm_api_key = "key"
            mock_settings.taxonomy_classification_model = "klai-fast"
            await maybe_generate_proposal("org1", "kb1", docs, nodes)

        mock_submit.assert_called_once()
        proposal = mock_submit.call_args.kwargs["proposal"]
        assert proposal.suggested_name == "Developer Resources"
        assert proposal.proposal_type == "new_node"
        assert proposal.document_count == 4


# ---------------------------------------------------------------------------
# Architectural regression tests — SPEC-TAXONOMY-V2-CONSOLIDATION-001.
#
# These don't call the LLM. They lock in *structural* properties of the
# prompt module so future drift gets caught at unit-test time:
#
# - All three active prompts compose the shared ``_NAMING_CRITERIA`` base
#   (the "common-theme over salient-brand" rule lands in one place).
# - The pre-Consolidation Unify-bug bias ("differentiate by what's UNIQUE")
#   is absent from the batched prompt — replaced by common-within-each-cluster
#   differentiation.
# - The deleted V1 symbols stay deleted (no accidental re-introduction).
# ---------------------------------------------------------------------------


class TestPromptArchitecture:
    def test_naming_criteria_states_common_theme_rule(self) -> None:
        from knowledge_ingest.proposal_generator import _NAMING_CRITERIA

        assert "COMMON across ALL documents" in _NAMING_CRITERIA, (
            "_NAMING_CRITERIA must explicitly state the common-theme rule"
        )
        assert "diverse providers" in _NAMING_CRITERIA, (
            "_NAMING_CRITERIA must give the multi-brand example so the LLM has a concrete pattern"
        )
        assert (
            "salient brand" in _NAMING_CRITERIA.lower()
            or "minority" in _NAMING_CRITERIA.lower()
            or "salient" in _NAMING_CRITERIA.lower()
        ), "_NAMING_CRITERIA must explicitly warn against salient-brand bias"

    def test_all_three_prompts_compose_naming_criteria(self) -> None:
        """Every active naming prompt must include the shared criteria base.

        Catches drift: if someone adds rules to one prompt without putting
        them in the base, this test fails.
        """
        from knowledge_ingest.proposal_generator import (
            _BATCHED_NAMING_SYSTEM_PROMPT_TEMPLATE,
            _BOOTSTRAP_V2_SYSTEM_PROMPT_TEMPLATE,
            _NAMING_CRITERIA,
            _PROPOSAL_SYSTEM_PROMPT,
        )

        # Pick a distinctive substring from the criteria base — if any prompt
        # rebuilds its rules locally instead of composing the base, this fails.
        marker = "COMMON across ALL documents"
        assert marker in _PROPOSAL_SYSTEM_PROMPT, "incremental prompt must compose criteria base"
        assert marker in _BOOTSTRAP_V2_SYSTEM_PROMPT_TEMPLATE, (
            "single-cluster prompt must compose criteria base"
        )
        assert marker in _BATCHED_NAMING_SYSTEM_PROMPT_TEMPLATE, (
            "batched prompt must compose criteria base"
        )
        # Sanity: the marker comes from the base, not coincidentally present elsewhere.
        assert marker in _NAMING_CRITERIA

    def test_batched_prompt_no_longer_has_unique_per_cluster_bias(self) -> None:
        """The Unify-bug came from this exact phrase. It must stay deleted."""
        from knowledge_ingest.proposal_generator import _BATCHED_NAMING_SYSTEM_PROMPT_TEMPLATE

        assert "what's UNIQUE about each" not in _BATCHED_NAMING_SYSTEM_PROMPT_TEMPLATE, (
            "batched prompt must not tell the LLM to label by what's UNIQUE per cluster — "
            "that biased it toward the most salient minority brand (Unify-bug)"
        )
        assert (
            "specific tool, specific use-case, specific audience"
            not in _BATCHED_NAMING_SYSTEM_PROMPT_TEMPLATE
        ), "batched prompt must not invite specific-item-per-cluster labels"

    def test_batched_prompt_keeps_distinctness_constraint(self) -> None:
        """The legitimate B4 fix (cross-cluster distinctness) must still be in
        the batched prompt — only the over-correction bias got removed."""
        from knowledge_ingest.proposal_generator import _BATCHED_NAMING_SYSTEM_PROMPT_TEMPLATE

        assert "DISTINCT" in _BATCHED_NAMING_SYSTEM_PROMPT_TEMPLATE, (
            "batched prompt must still enforce sibling distinctness — "
            "that's the original SPEC-TAXONOMY-V2-001-FOLLOWUP-001 B4 fix"
        )

    def test_v1_symbols_are_deleted(self) -> None:
        """V1 single-shot bootstrap path was deleted in
        SPEC-TAXONOMY-V2-CONSOLIDATION-001. No accidental re-introduction."""
        from knowledge_ingest import proposal_generator

        for forbidden in (
            "_BOOTSTRAP_SYSTEM_PROMPT",
            "generate_bootstrap_proposals",  # the v1 (non-_v2) function
            "_suggest_multiple_categories",
        ):
            attr = getattr(proposal_generator, forbidden, None)
            # generate_bootstrap_proposals_v2 is the v2 function, the bare name
            # is the deleted v1 — getattr returns the v2 if asked for v2, but
            # the v1 bare name should not exist.
            if forbidden == "generate_bootstrap_proposals":
                assert attr is None, (
                    f"V1 {forbidden} was deleted — do not re-introduce. "
                    "If you need the legacy single-shot path back, write a SPEC for it."
                )
            else:
                assert attr is None, f"V1 {forbidden} was deleted — do not re-introduce"
