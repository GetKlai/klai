# SPEC-PRIVACY-MISTRAL-PII-001 Phase 1 (REQ-3, REQ-4) — Klai's Dutch/credential
# recognizer pack, layered on the stock presidio-analyzer image. This adds
# config and two small Python files; it does not touch app.py, entrypoint.sh,
# or any vendored presidio_analyzer source.
#
# Base image: digest-pin this to the exact tag used in
# deploy/docker-compose.yml's presidio-analyzer service (kept in sync with
# Phase 0's pin — see that file's comment for why ghcr.io/data-privacy-stack
# and never mcr.microsoft.com/presidio-analyzer, which is frozen post-transition).
FROM ghcr.io/data-privacy-stack/presidio-analyzer@sha256:ae8f6f111ac2f04e3fec552f7f80edd0dcbfa2dd69ee1b9e030475be31669885

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
# The stock image's NLP_CONF_FILE and RECOGNIZER_REGISTRY_CONF_FILE defaults
# are left alone: since 2.2.364, analyzer_engine_provider.py's
# `_load_nlp_engine` / `_load_recognizer_registry` use an inline
# `nlp_configuration:` / `recognizer_registry:` section in ANALYZER_CONF_FILE
# before either per-section file. 2.2.362 had the opposite precedence, which
# made the stock defaults silently replace Klai's pack with English-only
# detection, so this image used to blank both variables. If a base bump ever
# reverses that precedence again, /supportedentities stops listing NL_BSN and
# the image tests in presidio-analyzer-image-build.yml go red.
ENV ANALYZER_CONF_FILE=/app/klai_analyzer.yaml
