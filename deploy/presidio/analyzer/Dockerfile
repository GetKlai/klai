# SPEC-PRIVACY-MISTRAL-PII-001 Phase 1 (REQ-3, REQ-4) — Klai's Dutch/credential
# recognizer pack, layered on the stock presidio-analyzer image. This adds
# config and two small Python files; it does not touch app.py, entrypoint.sh,
# or any vendored presidio_analyzer source.
#
# Base image: digest-pin this to the exact tag used in
# deploy/docker-compose.yml's presidio-analyzer service (kept in sync with
# Phase 0's pin — see that file's comment for why ghcr.io/data-privacy-stack
# and never mcr.microsoft.com/presidio-analyzer, which is frozen post-transition).
FROM ghcr.io/data-privacy-stack/presidio-analyzer@sha256:286e3fa7f3a7426e775e8564fe1870f1ba8f999d3ab8bbb8cc46a44355d9d6e9

# The stock image's WORKDIR is /app, where its vendored presidio_analyzer
# package lives (not pip-installed — see sitecustomize.py's module docstring).
WORKDIR /app

# Klai's recognizer pack + boot hook. sitecustomize.py is picked up
# automatically by the `site` module at interpreter start (see its own
# docstring for why that, and not an app.py change, is what wires this in).
#
# Copied into site-packages, NOT /app: `site.main()` processes
# sitecustomize.py before CPython finishes wiring the cwd (`''`) entry into
# sys.path for this image's `-c`/gunicorn-factory entry point — reproduced
# directly (an /app-relative COPY made `import sitecustomize` fail to be
# found at all, well before sitecustomize's own code could run its cwd
# workaround). site-packages is unconditionally on sys.path at that point.
COPY klai_pii_recognizers.py sitecustomize.py /usr/local/lib/python3.12/site-packages/
COPY conf/analyzer.yaml ./klai_analyzer.yaml

# ANALYZER_CONF_FILE points at Klai's unified config (supported_languages +
# nlp_configuration + recognizer_registry, all inline — see conf/analyzer.yaml
# for why every REQ-3 entity is registered per-language there rather than
# gated on a single `presidio_language`).
#
# NLP_CONF_FILE and RECOGNIZER_REGISTRY_CONF_FILE are set to the EMPTY STRING,
# not left at the stock image's defaults. This presidio-analyzer version
# (2.2.362 — see analyzer_engine_provider.py's `_load_nlp_engine` /
# `_load_recognizer_registry`) checks `self.nlp_engine_conf_file` /
# `self.recognizer_registry_conf_file` (from these two env vars) BEFORE it
# checks for an inline `nlp_configuration:` / `recognizer_registry:` section
# in ANALYZER_CONF_FILE — the opposite precedence from later Presidio
# releases, which fixed exactly this footgun ("a unified ANALYZER_CONF_FILE
# is self-contained and is not silently overridden by a per-section file
# baked into the image as a Dockerfile default" — verified directly against
# both the pinned 2.2.362 source and a newer sdist). Left at their stock
# defaults, the base image's `presidio_analyzer/conf/default.yaml` and
# `default_recognizers.yaml` win silently: no error, just Klai's pack not
# loaded and the analyzer falling back to stock English-only detection.
# Setting these two to "" makes them falsy, so the inline sections in
# ANALYZER_CONF_FILE are used instead — reproduced and confirmed against a
# running container before this comment was written.
ENV ANALYZER_CONF_FILE=/app/klai_analyzer.yaml \
    NLP_CONF_FILE="" \
    RECOGNIZER_REGISTRY_CONF_FILE=""
