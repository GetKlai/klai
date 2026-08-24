"""Source contracts for the pull-request docs image build gate."""

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
        if step.get("uses") == "docker/build-push-action@v7"
    )


def test_pull_requests_build_the_docs_image_without_push_permissions() -> None:
    verify = _jobs()["verify-image"]

    assert verify["needs"] == "quality-docs"
    assert verify["if"] == "github.event_name == 'pull_request'"
    assert verify["permissions"] == {"contents": "read"}
    assert not any(
        step.get("uses") == "docker/login-action@v4" for step in verify["steps"]
    )

    build = _build_step(verify)
    assert build["with"]["context"] == "./klai-docs"
    assert build["with"]["push"] is False


def test_production_docs_image_and_deploy_contract_is_unchanged() -> None:
    jobs = _jobs()
    publish = jobs["build-and-push"]

    assert publish["if"] == "github.event_name != 'pull_request'"
    assert _build_step(publish)["with"]["push"] is True
    assert jobs["scan"]["needs"] == "build-and-push"
    assert jobs["deploy"]["needs"] == ["build-and-push", "scan"]
