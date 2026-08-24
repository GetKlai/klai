"""Source contracts for the widget bundle's portal distribution path."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PORTAL_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "portal-frontend.yml"
QUALITY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "quality.yml"
SNIPPET_SOURCE = (
    REPO_ROOT
    / "klai-portal"
    / "frontend"
    / "src"
    / "features"
    / "widgets"
    / "embed"
    / "snippet.ts"
)
PLATFORM_DOC = REPO_ROOT / "docs" / "architecture" / "platform.md"
SCRIPT_URL = "https://my.getklai.com/widget/klai-chat.js"


def _workflow(path: Path) -> dict[str, object]:
    return yaml.load(path.read_text(), Loader=yaml.BaseLoader)


def test_every_widget_build_input_selects_portal_quality_and_deploy() -> None:
    portal = _workflow(PORTAL_WORKFLOW)
    assert "klai-widget/**" in portal["on"]["push"]["paths"]

    quality = _workflow(QUALITY_WORKFLOW)
    filters_source = next(
        step["with"]["filters"]
        for step in quality["jobs"]["changes"]["steps"]
        if step.get("uses") == "dorny/paths-filter@v4"
    )
    filters = yaml.safe_load(filters_source)
    assert "klai-widget/**" in filters["portal_frontend"]


def test_portal_build_verifies_and_copies_the_widget_bundle() -> None:
    workflow = _workflow(PORTAL_WORKFLOW)
    build = next(
        step
        for step in workflow["jobs"]["build-deploy"]["steps"]
        if step.get("name") == "Build widget bundle"
    )
    commands = build["run"]

    assert commands.index("npm run build") < commands.index("npm run check-size")
    assert commands.index("npm run check-size") < commands.index(
        "cp dist/klai-chat.js ../klai-portal/frontend/public/widget/klai-chat.js"
    )


def test_documented_and_product_snippets_use_the_served_portal_url() -> None:
    assert SCRIPT_URL in SNIPPET_SOURCE.read_text()
    assert SCRIPT_URL in PLATFORM_DOC.read_text()


def test_removed_nested_widget_workflow_stays_removed() -> None:
    assert not (
        REPO_ROOT / "klai-widget" / ".github" / "workflows" / "release.yml"
    ).exists()
