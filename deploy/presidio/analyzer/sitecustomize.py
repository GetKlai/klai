"""Boot hook for the Klai-layered presidio-analyzer image.

``sitecustomize.py`` is a standard CPython facility: the ``site`` module
imports it automatically at interpreter startup, before any application code
runs, if it is importable on ``sys.path`` (see
https://docs.python.org/3/library/site.html). Placing it in site-packages next
to the stock image's vendored ``presidio_analyzer`` package lets this image stay
"stock analyzer + layered config" — SPEC-PRIVACY-MISTRAL-PII-001's Phase 1
Deployment note — with zero changes to the upstream ``app.py`` or
``entrypoint.sh``.

Two things happen here, both required before ``app.py`` builds its
``AnalyzerEngine`` from ``ANALYZER_CONF_FILE``:

1. Import ``klai_pii_recognizers`` so its ``PatternRecognizer`` subclasses are
   registered as ``EntityRecognizer`` subclasses (Python subclass registration
   is a side effect of class definition/import, and persists in
   ``EntityRecognizer.__subclasses__()`` for the life of the process).
   ``RecognizerListLoader.get_existing_recognizer_cls`` — the function backing
   the YAML registry's ``type: predefined`` entries — looks classes up by name
   in that subclass tree, so this import must happen before
   ``AnalyzerEngineProvider(...).create_engine()`` runs, not after.

2. Patch ``SpacyNlpEngine.load`` to support a ``model_name: "blank"`` sentinel
   per language, so ``conf/analyzer.yaml`` can request ``spacy.blank(lang)``
   tokenizer-only pipelines instead of a trained model.

Why the patch, not a config-only path
--------------------------------------
This image is pinned to presidio-analyzer 2.2.362 (see
``deploy/docker-compose.yml``'s presidio-analyzer digest). That version's
``SpacyNlpEngine.load()`` unconditionally calls ``spacy.load(model_name)`` for
every configured language — there is no config-only way to request a blank,
untrained pipeline (later Presidio versions added a `slim` engine with a
`generic_tokenizer: "blank"` option, but even there the Docker-image config
surface, ``NlpEngineProvider.create_engine()``, never forwards
``supported_languages`` to the engine constructor for that shortcut to fire —
verified by reading the installed source, both the 2.2.362 image and a 2.2.363
sdist). Loading trained per-language models instead (`en_core_web_sm`,
`nl_core_news_sm`, ...) was measured at ~178ms per 10k-char document even with
NER and the parser disabled — three times over the SPEC's 60ms p95 NFR budget,
before this pack's own regex/checksum work even starts. `spacy.blank(lang)`
pipelines (tokenizer only, no trained weights) measured at ~0.6ms per document
in the same test. None of Klai's REQ-3 recognizers need real lemmas or POS tags
(NL_KVK does its own text-window context check — see klai_pii_recognizers.py —
specifically so it does not depend on NLP-engine tokenization quality), so a
blank pipeline is not a quality compromise for this pack; PERSON (the one
entity that would need real NLP) is out of scope for Phase 1 (REQ-2: GLiNER,
not spaCy, and a later PR).

This is a single, narrow method patch of one class, not a fork of the
dependency: it adds support for a documented-but-unwired concept
(``spacy.blank()`` fallback) rather than changing existing behavior for any
``model_name`` other than the literal string ``"blank"``. If a future
presidio-analyzer bump threads ``supported_languages``/``generic_tokenizer``
through the Docker config path natively, this patch becomes redundant (its
sentinel simply keeps working) rather than broken.
"""

from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger("presidio-analyzer")

# `site.main()` (which imports this module) runs before CPython finishes
# setting up `sys.path[0]` for `-c`/module-run/gunicorn entry points on some
# CPython builds, so the `''` (cwd) entry that would normally make the stock
# image's vendored `/app/presidio_analyzer` package importable is not always
# in place yet at this point — verified empirically against this exact image
# (ghcr.io/data-privacy-stack/presidio-analyzer, Python 3.12): a plain
# `import presidio_analyzer` from application code works, but the identical
# import fails here with `sitecustomize` swallowing the traceback, unless the
# working directory is added explicitly first.
if os.getcwd() not in sys.path:
    sys.path.insert(0, os.getcwd())

# --- 1. Register the Klai recognizer pack --------------------------------
import klai_pii_recognizers  # noqa: F401,E402  (import-for-side-effect)

# --- 2. Patch SpacyNlpEngine to support model_name: "blank" ---------------
try:
    import spacy
    from presidio_analyzer.nlp_engine import SpacyNlpEngine

    def _load_with_blank_support(self) -> None:  # noqa: D401
        """Load spaCy pipelines, using spacy.blank(lang) for model_name == 'blank'."""
        self._enable_gpu()
        self.nlp = {}
        for model in self.models:
            self._validate_model_params(model)
            lang_code = model["lang_code"]
            model_name = model["model_name"]
            if model_name == "blank":
                self.nlp[lang_code] = spacy.blank(lang_code)
                logger.info(
                    "klai_pii: loaded blank (tokenizer-only) spaCy pipeline "
                    "for language '%s' — no trained model, no NER, REQ-2",
                    lang_code,
                )
                continue
            self._download_spacy_model_if_needed(model_name)
            self.nlp[lang_code] = spacy.load(model_name)

    SpacyNlpEngine.load = _load_with_blank_support
except ImportError:  # pragma: no cover - spaCy is a base-image dependency
    logger.warning(
        "klai_pii: spaCy not importable; SpacyNlpEngine blank-pipeline patch skipped"
    )
