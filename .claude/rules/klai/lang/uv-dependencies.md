---
paths:
  - "**/pyproject.toml"
  - "**/uv.lock"
  - "**/Dockerfile*"
---
# uv path dependencies

`[tool.uv.sources]` is uv project metadata, not a standard requirements-file
feature. `uv pip install -r pyproject.toml` can therefore miss local/path source
overrides and fall through to a package index.

For a service with `[tool.uv.sources]`, build from the repository/workspace
context that contains every referenced path and prefer the locked project flow
used by the existing service images: `uv sync --frozen` (with the project's
intentional flags). An editable install is acceptable only where its Dockerfile
documents and tests the exact path-dependency strategy.

Services without `[tool.uv.sources]` may still use `uv pip -r pyproject.toml`;
do not mechanically rewrite them. Verify dependency changes with the actual
Docker build, because a host checkout can hide missing Docker build context.
