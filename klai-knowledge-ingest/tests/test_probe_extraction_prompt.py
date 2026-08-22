from unittest.mock import patch

import pytest

from scripts import probe_extraction_prompt as probe


def test_marker_count_counts_baseline_facts_containing_each_marker():
    facts = [
        "Een van de documentatieartikelen voor Freedom is getiteld 'Freedom: Het Dashboard'.",
        "De handleiding 'Wachtrijstatistieken' beschrijft statistieken over "
        "wachtrijen binnen Freedom.",
        "Het onderwerp van de handleiding 'Statistieken' zijn statistische overzichten in Freedom.",
        "VoIP-bureautelefoons vallen onder de apparatuursectie in de Voys-documentatie.",
    ]

    assert probe._marker_count(facts, "documentatieartikel") == 1
    assert probe._marker_count(facts, "sectie") == 1
    assert probe._marker_count(facts, "documentatie") == 2
    assert probe._marker_count(facts, "handleiding") == 2
    assert probe._marker_count(facts, " de ") == 4


def test_marker_count_counts_one_fact_with_marker_at_start_and_end_once():
    assert probe._marker_count(["De instructie eindigt met de"], " de ") == 1


def test_probe_requires_validation_scratch_prefix():
    with pytest.raises(ValueError, match="zz-prompt-validation-"):
        probe.validate_probe_ids("source-org", "scratch-org")


def test_probe_refuses_to_replay_into_source_graph():
    org_id = "zz-prompt-validation-same"

    with pytest.raises(ValueError, match="must differ"):
        probe.validate_probe_ids(org_id, org_id)


def test_delete_rechecks_validation_scratch_prefix():
    with (
        patch.object(probe.graph_module, "wipe_org_graph") as wipe,
        pytest.raises(ValueError, match="zz-prompt-validation-"),
    ):
        probe.delete_scratch_graph("customer-org")

    wipe.assert_not_called()


def test_delete_targets_and_verifies_only_the_scratch_graph():
    scratch_org_id = "zz-prompt-validation-1148"

    with (
        patch.object(probe.graph_module, "wipe_org_graph", return_value=7) as wipe,
        patch.object(probe, "count_graph_nodes", return_value=0) as count_nodes,
    ):
        deleted = probe.delete_scratch_graph(scratch_org_id)

    assert deleted == 7
    wipe.assert_called_once_with(scratch_org_id)
    count_nodes.assert_called_once_with(scratch_org_id)


@pytest.mark.asyncio
async def test_probe_failure_still_deletes_the_scratch_graph():
    scratch_org_id = "zz-prompt-validation-mid-run"

    with (
        patch.object(probe.settings, "graphiti_enabled", True),
        patch.object(probe, "load_documents", side_effect=RuntimeError("probe failed")),
        patch.object(probe, "delete_scratch_graph", return_value=0) as delete,
        pytest.raises(RuntimeError, match="probe failed"),
    ):
        await probe.run("source-org", ["support"], 5, scratch_org_id)

    delete.assert_called_once_with(scratch_org_id)


@pytest.mark.asyncio
async def test_cleanup_failure_names_the_scratch_graph_left_behind():
    scratch_org_id = "zz-prompt-validation-cleanup-failure"

    with (
        patch.object(probe.settings, "graphiti_enabled", True),
        patch.object(probe, "load_documents", side_effect=RuntimeError("probe failed")),
        patch.object(probe, "delete_scratch_graph", side_effect=RuntimeError("delete failed")),
        pytest.raises(RuntimeError, match=rf"{scratch_org_id!s}.*left behind"),
    ):
        await probe.run("source-org", ["support"], 5, scratch_org_id)
