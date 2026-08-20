"""Static checks on the deployment wiring itself (SPEC-PRIVACY-MISTRAL-PII-001
Phase 1's Deployment section / AC-2). No network, no Docker: these just parse
text files already in the repo.
"""

from __future__ import annotations

import re

import pytest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_COMPOSE_FILE = _REPO_ROOT / "deploy" / "docker-compose.yml"
_DOCKERFILE = Path(__file__).resolve().parent.parent / "analyzer" / "Dockerfile"

_DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}\b")


def _service_block(compose_text: str, service: str) -> str:
    """Return the YAML block for a top-level service (crude but sufficient:
    from the service's own line up to the next line at the same 2-space
    indent, or EOF)."""
    lines = compose_text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"  {service}:"))
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"^  \S", lines[i]):
            end = i
            break
    return "\n".join(lines[start:end])


_BOOTSTRAP_PENDING = pytest.mark.xfail(
    strict=True,
    reason=(
        "Bootstrap ordering: presidio-analyzer-image-build.yml only publishes on a "
        "push to main, so ghcr.io/getklai/presidio-analyzer does not exist until the "
        "PR carrying its Dockerfile has merged. Compose therefore still runs the "
        "stock image at the stock 2G limit. strict=True is the point: the moment the "
        "follow-up PR flips image and limit together, these XPASS and CI fails until "
        "this marker is deleted — so the target state cannot be forgotten."
    ),
)


class TestComposeImagePinning:
    """AC-2: both containers pinned by digest, both from
    ghcr.io/data-privacy-stack — neither from mcr.microsoft.com. Phase 1
    layers presidio-analyzer's image under ghcr.io/getklai/*, still built
    FROM the ghcr.io/data-privacy-stack digest (checked via the Dockerfile,
    not the compose file, for that service)."""

    @_BOOTSTRAP_PENDING
    def test_presidio_analyzer_uses_klai_image_pinned_by_digest(self):
        text = _COMPOSE_FILE.read_text()
        block = _service_block(text, "presidio-analyzer")
        image_line = next(line for line in block.splitlines() if "image:" in line)
        assert "ghcr.io/getklai/presidio-analyzer@sha256:" in image_line
        assert _DIGEST_RE.search(image_line), image_line

    def test_presidio_anonymizer_still_pinned_to_stock_image(self):
        text = _COMPOSE_FILE.read_text()
        block = _service_block(text, "presidio-anonymizer")
        image_line = next(line for line in block.splitlines() if "image:" in line)
        assert "ghcr.io/data-privacy-stack/presidio-anonymizer@sha256:" in image_line
        assert _DIGEST_RE.search(image_line), image_line

    def test_neither_service_references_frozen_microsoft_registry(self):
        text = _COMPOSE_FILE.read_text()
        for service in ("presidio-analyzer", "presidio-anonymizer"):
            block = _service_block(text, service)
            assert "mcr.microsoft.com" not in block, service

    def test_presidio_analyzer_no_caddy_route_no_published_port(self):
        text = _COMPOSE_FILE.read_text()
        block = _service_block(text, "presidio-analyzer")
        assert "ports:" not in block
        assert re.search(r"networks:\s*\n\s*- inference\b", block), block

    @_BOOTSTRAP_PENDING
    def test_presidio_analyzer_has_explicit_resource_limits(self):
        text = _COMPOSE_FILE.read_text()
        block = _service_block(text, "presidio-analyzer")
        assert "cpus:" in block
        assert "memory:" in block
        # The revised limit must be smaller than the old provisional 2G —
        # REQ-2's "no spaCy model" claim is supposed to buy a real reduction,
        # not a cosmetic one.
        memory_line = next(line for line in block.splitlines() if "memory:" in line)
        value = memory_line.split("memory:")[1].strip()
        assert value.endswith(("M", "G"))
        if value.endswith("G"):
            assert float(value[:-1]) < 2.0, value


class TestDockerfileBase:
    def test_from_is_pinned_stock_data_privacy_stack_image(self):
        text = _DOCKERFILE.read_text()
        from_line = next(line for line in text.splitlines() if line.startswith("FROM "))
        assert "ghcr.io/data-privacy-stack/presidio-analyzer@sha256:" in from_line
        assert _DIGEST_RE.search(from_line), from_line
        # A comment may explain the rule by naming the forbidden registry;
        # only the actual FROM line matters (mirrors
        # check-klai-librechat-digest.sh's own "comments don't trip the
        # rule" behaviour).
        assert "mcr.microsoft.com" not in from_line
