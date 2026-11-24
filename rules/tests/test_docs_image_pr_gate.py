"""Source contracts for the docs image build and scan on pull requests."""

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "docs.yml"


def _jobs() -> dict[str, object]:
    workflow = yaml.safe_load(DOCS_WORKFLOW.read_text())
    return workflow["jobs"]


def _build_step(job: dict[str, object]) -> dict[str, object]:
    return next(
        step
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("docker/build-push-action@")
    )


def test_pull_requests_build_push_the_sha_tag_and_scan_the_docs_image() -> None:
    jobs = _jobs()
    publish = jobs["build-and-push"]

    assert "verify-image" not in jobs
    assert "if" not in publish
    assert publish["needs"] == "quality-docs"
    assert "if" not in jobs["scan"]
    assert jobs["scan"]["needs"] == "build-and-push"

    tags = _build_step(publish)["with"]["tags"]
    assert "github.sha" in tags
    assert "github.ref == 'refs/heads/main'" in tags


def test_production_docs_image_and_deploy_contract_is_unchanged() -> None:
    jobs = _jobs()

    assert _build_step(jobs["build-and-push"])["with"]["push"] is True
    assert jobs["deploy"]["if"] == "github.ref == 'refs/heads/main'"
    assert jobs["deploy"]["needs"] == ["build-and-push", "scan"]
